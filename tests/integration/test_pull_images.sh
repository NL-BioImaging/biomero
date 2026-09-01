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
    BIOMERO_PULL_ATTEMPTS=3 \
    SLURM_ARRAY_JOB_ID=100 \
    SLURM_ARRAY_TASK_ID=0 \
    SLURM_TMPDIR="$case_dir/slurm-tmp" \
        bash "$runner" "$case_dir/manifest.tsv" "$case_dir/status"
}

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

echo 'pull_images integration checks passed'
