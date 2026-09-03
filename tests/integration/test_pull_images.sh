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
        case ${FAKE_RUNTIME_MODE:-success} in
            streaming)
                echo 'runtime progress is visible'
                touch "${FAKE_RUNTIME_STARTED:?}"
                sleep 3
                ;;
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
    FAKE_RUNTIME_NAME="$case_dir/runtime-name" \
    FAKE_RUNTIME_STARTED="$case_dir/runtime-started" \
    BIOMERO_PULL_ATTEMPTS=3 \
    SLURM_ARRAY_JOB_ID=100 \
    SLURM_ARRAY_TASK_ID=0 \
    SLURM_TMPDIR="$case_dir/slurm-tmp" \
        bash "$runner" "$case_dir/manifest.tsv" "$case_dir/status"
}

streaming_dir="$test_root/streaming"
run_task "$streaming_dir" streaming > "$streaming_dir-output.log" 2>&1 &
streaming_pid=$!
for _ in $(seq 1 50); do
    [ ! -e "$streaming_dir/runtime-started" ] || break
    sleep 0.02
done
test -e "$streaming_dir/runtime-started"
if ! grep -q 'runtime progress is visible' "$streaming_dir-output.log"; then
    kill "$streaming_pid" 2>/dev/null || true
    wait "$streaming_pid" 2>/dev/null || true
    echo 'runtime build output was buffered instead of streamed' >&2
    exit 1
fi
wait "$streaming_pid"

success_dir="$test_root/success"
run_task "$success_dir" success
test "$(cat "$success_dir/runtime-name")" = apptainer
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

echo 'pull_images integration checks passed'
