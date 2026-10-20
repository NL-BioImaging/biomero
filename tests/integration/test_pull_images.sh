#!/bin/bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner="$repo_root/resources/pull_images.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fake_bin="$test_root/bin"
mkdir -p "$fake_bin"

cat > "$fake_bin/apptainer" <<'EOF'
#!/bin/bash
if [ -n "${FAKE_RUNTIME_NAME:-}" ]; then
    basename "$0" > "$FAKE_RUNTIME_NAME"
fi
case $1 in
    inspect)
        [ -s "$2" ]
        ;;
    build)
        count_file=${FAKE_BUILD_COUNT:?}
        count=0
        [ ! -f "$count_file" ] || count=$(cat "$count_file")
        count=$((count + 1))
        printf '%s\n' "$count" > "$count_file"
        printf '%s\n' "$*" > "${FAKE_RUNTIME_ARGS:?}"
        source=${@: -1}
        if [[ "$source" != docker://* ]]; then
            # Like Apptainer, resolve %files host paths from the build's cwd,
            # not from the definition's location.
            host_source=$(awk '/^%files/ { getline; print $1; exit }' "$source")
            if ! cp -- "$host_source" "${FAKE_COPIED_SOURCE:?}"; then
                echo "FATAL: cannot copy %files source: $host_source" >&2
                exit 255
            fi
            pwd -P > "${FAKE_BUILD_CWD:?}"
            [ -d "$APPTAINER_TMPDIR" ] && [ -d "$APPTAINER_CACHEDIR" ] || exit 73
        fi
        case ${FAKE_RUNTIME_MODE:-success} in
            permanent)
                echo 'FATAL: manifest unknown' >&2
                exit 22
                ;;
            transient)
                if [ "$count" -lt 3 ]; then
                    echo 'registry request timed out' >&2
                    exit 28
                fi
                ;;
            transient-exhausted)
                echo 'registry request timed out' >&2
                exit 28
                ;;
        esac
        destination=${@: -2:1}
        printf 'valid-sif\n' > "$destination"
        ;;
    *)
        exit 64
        ;;
esac
EOF
chmod +x "$fake_bin/apptainer"

write_manifest() {
    manifest=$1
    destination=$2
    printf 'kind\tname\tversion\tsource_type\tsource\tdestination\n' > "$manifest"
    printf 'workflow\timagej\tv1\tregistry\texample/imagej\t%s\n' \
        "$destination" >> "$manifest"
}

run_task() {
    case_dir=$1
    mode=$2
    mkdir -p "$case_dir/status" "$case_dir/slurm-tmp"
    write_manifest "$case_dir/manifest.tsv" "$case_dir/shared/imagej_v1.sif"
    PATH="$fake_bin:$PATH" \
    FAKE_RUNTIME_MODE="$mode" \
    FAKE_BUILD_COUNT="$case_dir/build-count" \
    FAKE_RUNTIME_ARGS="$case_dir/runtime-args" \
    FAKE_RUNTIME_NAME="$case_dir/runtime-name" \
    BIOMERO_PULL_ATTEMPTS=3 \
    SLURM_ARRAY_JOB_ID=100 \
    SLURM_ARRAY_TASK_ID=0 \
    SLURM_TMPDIR="$case_dir/slurm-tmp" \
        bash "$runner" "$case_dir/manifest.tsv" "$case_dir/status"
}

success_dir="$test_root/success"
run_task "$success_dir" success
test "$(cat "$success_dir/runtime-name")" = apptainer
grep -q -- '-processors 1' "$success_dir/runtime-args"
test -s "$success_dir/shared/imagej_v1.sif"
grep -q $'workflow\timagej\tv1\tREADY\t0\tbuilt and validated' \
    "$success_dir/status/status-0.status"
test -z "$(find "$success_dir/slurm-tmp" -mindepth 1 -print -quit)"
test -z "$(find "$success_dir/shared" -name '*.partial.*' -print -quit)"

permanent_dir="$test_root/permanent"
if run_task "$permanent_dir" permanent; then
    echo 'permanent manifest error unexpectedly succeeded' >&2
    exit 1
fi
test "$(cat "$permanent_dir/build-count")" = 1
grep -q $'workflow\timagej\tv1\tFAILED\t22\tmanifest unknown' \
    "$permanent_dir/status/status-0.status"
test ! -e "$permanent_dir/shared/imagej_v1.sif"
test -z "$(find "$permanent_dir/slurm-tmp" -mindepth 1 -print -quit)"

transient_dir="$test_root/transient"
run_task "$transient_dir" transient
test "$(cat "$transient_dir/build-count")" = 3
grep -q $'workflow\timagej\tv1\tREADY\t0\tbuilt and validated' \
    "$transient_dir/status/status-0.status"
test -z "$(find "$transient_dir/slurm-tmp" -mindepth 1 -print -quit)"

skip_dir="$test_root/skip"
mkdir -p "$skip_dir/shared" "$skip_dir/status" "$skip_dir/slurm-tmp"
printf 'valid-sif\n' > "$skip_dir/shared/imagej_v1.sif"
write_manifest "$skip_dir/manifest.tsv" "$skip_dir/shared/imagej_v1.sif"
PATH="$fake_bin:$PATH" \
FAKE_RUNTIME_NAME="$skip_dir/runtime-name" \
SLURM_ARRAY_JOB_ID=101 \
SLURM_ARRAY_TASK_ID=0 \
SLURM_TMPDIR="$skip_dir/slurm-tmp" \
    bash "$runner" "$skip_dir/manifest.tsv" "$skip_dir/status"
test ! -e "$skip_dir/build-count"
test "$(cat "$skip_dir/runtime-name")" = apptainer
grep -q $'workflow\timagej\tv1\tREADY\t0\texisting SIF validated' \
    "$skip_dir/status/status-0.status"

fallback_dir="$test_root/singularity-fallback"
fallback_bin="$fallback_dir/bin"
mkdir -p "$fallback_bin" "$fallback_dir/shared" "$fallback_dir/status" \
    "$fallback_dir/slurm-tmp"
cp "$fake_bin/apptainer" "$fallback_bin/singularity"
printf 'valid-sif\n' > "$fallback_dir/shared/imagej_v1.sif"
write_manifest "$fallback_dir/manifest.tsv" \
    "$fallback_dir/shared/imagej_v1.sif"
PATH="$fallback_bin:$PATH" \
FAKE_RUNTIME_NAME="$fallback_dir/runtime-name" \
SLURM_ARRAY_JOB_ID=102 \
SLURM_ARRAY_TASK_ID=0 \
SLURM_TMPDIR="$fallback_dir/slurm-tmp" \
    bash "$runner" "$fallback_dir/manifest.tsv" "$fallback_dir/status"
test "$(cat "$fallback_dir/runtime-name")" = singularity
grep -q $'workflow\timagej\tv1\tREADY\t0\texisting SIF validated' \
    "$fallback_dir/status/status-0.status"

missing_dir="$test_root/missing-runtime"
mkdir -p "$missing_dir/shared" "$missing_dir/status" "$missing_dir/slurm-tmp"
write_manifest "$missing_dir/manifest.tsv" "$missing_dir/shared/imagej_v1.sif"
if PATH="/usr/bin:/bin" \
    SLURM_ARRAY_JOB_ID=103 \
    SLURM_ARRAY_TASK_ID=0 \
    SLURM_TMPDIR="$missing_dir/slurm-tmp" \
        bash "$runner" "$missing_dir/manifest.tsv" "$missing_dir/status"; then
    echo 'missing container runtime unexpectedly succeeded' >&2
    exit 1
fi
grep -q $'workflow\timagej\tv1\tFAILED\t69\tApptainer or Singularity is required' \
    "$missing_dir/status/status-0.status"

# Build output must stream to the job log as it happens (so `tail -f` shows
# progress), not only get flushed once the whole attempt has finished.
stream_dir="$test_root/streaming"
stream_bin="$stream_dir/bin"
mkdir -p "$stream_bin" "$stream_dir/shared" "$stream_dir/status" \
    "$stream_dir/slurm-tmp"
started_marker="$stream_dir/build-started"
continue_marker="$stream_dir/build-continue"
cat > "$stream_bin/apptainer" <<EOF
#!/bin/bash
case \$1 in
    inspect)
        [ -s "\$2" ]
        ;;
    build)
        echo 'INFO: Starting build...'
        touch "$started_marker"
        while [ ! -f "$continue_marker" ]; do
            sleep 0.1
        done
        echo 'INFO: Build complete'
        destination=\${@: -2:1}
        printf 'valid-sif\n' > "\$destination"
        ;;
    *)
        exit 64
        ;;
esac
EOF
chmod +x "$stream_bin/apptainer"
write_manifest "$stream_dir/manifest.tsv" "$stream_dir/shared/imagej_v1.sif"
job_log="$stream_dir/pull-image-0.log"
PATH="$stream_bin:$PATH" \
SLURM_ARRAY_JOB_ID=104 \
SLURM_ARRAY_TASK_ID=0 \
SLURM_TMPDIR="$stream_dir/slurm-tmp" \
    bash "$runner" "$stream_dir/manifest.tsv" "$stream_dir/status" \
    > "$job_log" 2>&1 &
runner_pid=$!

waited=0
while [ ! -f "$started_marker" ]; do
    sleep 0.1
    waited=$((waited + 1))
    if [ "$waited" -ge 50 ]; then
        echo 'fake build never started' >&2
        echo '--- job log ---' >&2
        cat "$job_log" >&2 || true
        echo '--- status dir ---' >&2
        ls -la "$stream_dir/status" >&2 || true
        cat "$stream_dir/status/status-0.status" >&2 || true
        kill "$runner_pid" 2>/dev/null || true
        exit 1
    fi
done

if ! grep -q 'INFO: Starting build' "$job_log"; then
    echo 'build output was not streamed live to the job log' >&2
    kill "$runner_pid" 2>/dev/null || true
    exit 1
fi

touch "$continue_marker"
wait "$runner_pid"
grep -q $'workflow\timagej\tv1\tREADY\t0\tbuilt and validated' \
    "$stream_dir/status/status-0.status"

# The bundled converter's %files entry is relative to its definition.
# Run from an unrelated cwd, with spaces in paths and relative worker paths,
# so changing the build cwd cannot redirect logs, cache, or SIF publication.
run_definition_task() (
    case_dir=$1
    mode=$2
    source_mode=${3:-absolute}
    runtime=${4:-apptainer}
    mkdir -p "$case_dir/converter sources" "$case_dir/bin"
    cp "$repo_root/resources/convert_zarr_to_tiff.def" \
        "$case_dir/converter sources/converter.def"
    cp "$repo_root/resources/convert_zarr_to_tiff.py" \
        "$case_dir/converter sources/convert_zarr_to_tiff.py"
    cp "$fake_bin/apptainer" "$case_dir/bin/$runtime"
    definition_source="$case_dir/converter sources/converter.def"
    case "$source_mode" in
        relative) definition_source="converter sources/converter.def" ;;
        symlink)
            ln -s "converter sources/converter.def" "$case_dir/linked.def"
            definition_source=linked.def
            ;;
    esac
    if [ "$mode" = missing-source ]; then
        rm "$case_dir/converter sources/convert_zarr_to_tiff.py"
    elif [ "$mode" = missing-definition ]; then
        rm "$case_dir/converter sources/converter.def"
    fi
    printf 'kind\tname\tversion\tsource_type\tsource\tdestination\n' \
        > "$case_dir/manifest.tsv"
    printf 'converter\tzarr_to_tiff\tlatest\tdefinition\t%s\tshared/converter.sif\n' \
        "$definition_source" >> "$case_dir/manifest.tsv"
    cd "$case_dir"
    PATH="$case_dir/bin:$PATH" \
    FAKE_RUNTIME_MODE="$mode" \
    FAKE_BUILD_COUNT="$case_dir/build-count" \
    FAKE_RUNTIME_ARGS="$case_dir/runtime-args" \
    FAKE_RUNTIME_NAME="$case_dir/runtime-name" \
    FAKE_COPIED_SOURCE="$case_dir/copied-source.py" \
    FAKE_BUILD_CWD="$case_dir/build-cwd" \
    BIOMERO_PULL_ATTEMPTS=3 \
    SLURM_ARRAY_JOB_ID=105 \
    SLURM_ARRAY_TASK_ID=0 \
    SLURM_TMPDIR="" \
        bash "$runner" manifest.tsv status "local tmp" "local cache"
)

assert_definition_ready() {
    case_dir=$1
    test -s "$case_dir/shared/converter.sif"
    cmp "$repo_root/resources/convert_zarr_to_tiff.py" "$case_dir/copied-source.py"
    test "$(cat "$case_dir/build-cwd")" = \
        "$(cd "$case_dir/converter sources" && pwd -P)"
    grep -q $'converter\tzarr_to_tiff\tlatest\tREADY\t0\tbuilt and validated' \
        "$case_dir/status/status-0.status"
    test -z "$(find "$case_dir/local tmp" "$case_dir/local cache" -mindepth 1 -print -quit)"
    test -z "$(find "$case_dir/shared" -name '*.partial.*' -print -quit)"
}

definition_dir="$test_root/definition absolute"
run_definition_task "$definition_dir" success
assert_definition_ready "$definition_dir"
test "$(cat "$definition_dir/build-count")" = 1
test "$(cat "$definition_dir/runtime-name")" = apptainer

relative_dir="$test_root/definition relative"
run_definition_task "$relative_dir" success relative
assert_definition_ready "$relative_dir"

symlink_dir="$test_root/definition symlink"
run_definition_task "$symlink_dir" success symlink
assert_definition_ready "$symlink_dir"

singularity_dir="$test_root/definition singularity"
run_definition_task "$singularity_dir" success relative singularity
assert_definition_ready "$singularity_dir"
test "$(cat "$singularity_dir/runtime-name")" = singularity

definition_retry_dir="$test_root/definition retry"
run_definition_task "$definition_retry_dir" transient
assert_definition_ready "$definition_retry_dir"
test "$(cat "$definition_retry_dir/build-count")" = 3

definition_fail_dir="$test_root/definition exhausted"
if run_definition_task "$definition_fail_dir" transient-exhausted; then
    echo 'failed definition build unexpectedly succeeded' >&2
    exit 1
fi
test "$(cat "$definition_fail_dir/build-count")" = 3
grep -q $'converter\tzarr_to_tiff\tlatest\tFAILED\t28\tregistry/network timeout' \
    "$definition_fail_dir/status/status-0.status"
test ! -e "$definition_fail_dir/shared/converter.sif"
test -z "$(find "$definition_fail_dir/local tmp" "$definition_fail_dir/local cache" -mindepth 1 -print -quit)"

missing_source_dir="$test_root/definition missing source"
if run_definition_task "$missing_source_dir" missing-source; then
    echo 'missing %files source unexpectedly succeeded' >&2
    exit 1
fi
test "$(cat "$missing_source_dir/build-count")" = 1
grep -q $'FAILED\t255\tFATAL: cannot copy %files source: convert_zarr_to_tiff.py' \
    "$missing_source_dir/status/status-0.status"
test ! -e "$missing_source_dir/shared/converter.sif"

missing_definition_dir="$test_root/definition missing"
if run_definition_task "$missing_definition_dir" missing-definition; then
    echo 'missing definition unexpectedly succeeded' >&2
    exit 1
fi
test ! -e "$missing_definition_dir/build-count"
grep -q $'FAILED\t66\tconverter definition is missing or unreadable' \
    "$missing_definition_dir/status/status-0.status"

echo 'pull_images integration checks passed'
