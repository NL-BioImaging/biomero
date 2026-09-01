#!/bin/bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
runner="$repo_root/resources/pull_images.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fake_bin="$test_root/bin"
mkdir -p "$fake_bin"

cat > "$fake_bin/skopeo" <<'EOF'
#!/bin/bash
count_file=${FAKE_SKOPEO_COUNT:?}
count=0
[ ! -f "$count_file" ] || count=$(cat "$count_file")
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
case ${FAKE_SKOPEO_MODE:-success} in
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
printf '{"schemaVersion":2}\n'
EOF

cat > "$fake_bin/singularity" <<'EOF'
#!/bin/bash
case $1 in
    inspect)
        [ -s "$2" ]
        ;;
    build)
        destination=${@: -2:1}
        printf 'valid-sif\n' > "$destination"
        ;;
    *)
        exit 64
        ;;
esac
EOF
chmod +x "$fake_bin/skopeo" "$fake_bin/singularity"

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
    FAKE_SKOPEO_MODE="$mode" \
    FAKE_SKOPEO_COUNT="$case_dir/skopeo-count" \
    BIOMERO_PULL_ATTEMPTS=3 \
    SLURM_ARRAY_JOB_ID=100 \
    SLURM_ARRAY_TASK_ID=0 \
    SLURM_TMPDIR="$case_dir/slurm-tmp" \
        bash "$runner" "$case_dir/manifest.tsv" "$case_dir/status"
}

success_dir="$test_root/success"
run_task "$success_dir" success
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
test "$(cat "$permanent_dir/skopeo-count")" = 1
grep -q $'workflow\timagej\tv1\tFAILED\t22\tmanifest unknown' \
    "$permanent_dir/status/status-0.status"
test ! -e "$permanent_dir/shared/imagej_v1.sif"
test -z "$(find "$permanent_dir/slurm-tmp" -mindepth 1 -print -quit)"

transient_dir="$test_root/transient"
run_task "$transient_dir" transient
test "$(cat "$transient_dir/skopeo-count")" = 3
grep -q $'workflow\timagej\tv1\tREADY\t0\tbuilt and validated' \
    "$transient_dir/status/status-0.status"
test -z "$(find "$transient_dir/slurm-tmp" -mindepth 1 -print -quit)"

skip_dir="$test_root/skip"
mkdir -p "$skip_dir/shared" "$skip_dir/status" "$skip_dir/slurm-tmp"
printf 'valid-sif\n' > "$skip_dir/shared/imagej_v1.sif"
write_manifest "$skip_dir/manifest.tsv" "$skip_dir/shared/imagej_v1.sif"
PATH="$fake_bin:$PATH" \
FAKE_SKOPEO_COUNT="$skip_dir/skopeo-count" \
SLURM_ARRAY_JOB_ID=101 \
SLURM_ARRAY_TASK_ID=0 \
SLURM_TMPDIR="$skip_dir/slurm-tmp" \
    bash "$runner" "$skip_dir/manifest.tsv" "$skip_dir/status"
test ! -e "$skip_dir/skopeo-count"
grep -q $'workflow\timagej\tv1\tREADY\t0\texisting SIF validated' \
    "$skip_dir/status/status-0.status"

echo 'pull_images integration checks passed'
