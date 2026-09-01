#!/bin/bash
set -uo pipefail

manifest_path=${1:?image manifest path is required}
status_dir=${2:?status directory is required}
configured_tmpdir=${3:-}
configured_cachedir=${4:-}
task_id=${SLURM_ARRAY_TASK_ID:?SLURM_ARRAY_TASK_ID is required}
array_job_id=${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-unknown}}
max_attempts=${BIOMERO_PULL_ATTEMPTS:-3}

case "$max_attempts" in
    ''|*[!0-9]*|0) max_attempts=3 ;;
esac

mkdir -p -- "$status_dir"

# The first manifest row is a header; array task 0 consumes row 2.
manifest_row=$(awk -v row="$((task_id + 2))" 'NR == row { print; exit }' "$manifest_path")
if [ -z "$manifest_row" ]; then
    echo "No manifest row for array task $task_id" >&2
    exit 64
fi

IFS=$'\t' read -r image_kind image_name image_version source_type source destination <<< "$manifest_row"
status_file="$status_dir/status-${task_id}.status"
shared_partial="${destination}.partial.${array_job_id}_${task_id}"
work_parent=${SLURM_TMPDIR:-${configured_tmpdir:-${APPTAINER_TMPDIR:-${SINGULARITY_TMPDIR:-${TMPDIR:-/tmp}}}}}
work_dir=""
task_cache=""
final_state=""

sanitize_reason() {
    printf '%s' "$1" | tr '\t\r\n' '   ' | cut -c1-240
}

write_status() {
    state=$1
    exit_code=$2
    reason=$(sanitize_reason "$3")
    status_tmp="${status_file}.tmp.$$"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$image_kind" "$image_name" "$image_version" "$state" \
        "$exit_code" "$reason" "$destination" > "$status_tmp"
    mv -- "$status_tmp" "$status_file"
}

cleanup() {
    rc=$?
    rm -f -- "$shared_partial"
    if [ -n "$work_dir" ]; then
        rm -rf -- "$work_dir"
    fi
    if [ -n "$task_cache" ] && [ "$task_cache" != "$work_dir/cache" ]; then
        rm -rf -- "$task_cache"
    fi
    if [ "$rc" -ne 0 ] && [ -z "$final_state" ]; then
        final_state=FAILED
        write_status FAILED "$rc" "unexpected task failure"
    fi
}
trap cleanup EXIT

fail_task() {
    code=$1
    shift
    final_state=FAILED
    write_status FAILED "$code" "$*"
    exit "$code"
}

is_permanent_error() {
    grep -Eqi \
        'manifest unknown|name unknown|requested access.*denied|unauthorized|authentication required|unsupported media type|invalid reference format' \
        "$1"
}

is_transient_error() {
    grep -Eqi \
        '(^|[^0-9])429([^0-9]|$)|too many requests|(^|[^0-9])5[0-9][0-9]([^0-9]|$)|timed out|timeout|connection reset|temporary failure|TLS handshake timeout|unexpected EOF|connection refused|network is unreachable|no such host|could not resolve host|i/o timeout|context deadline exceeded' \
        "$1"
}

concise_reason() {
    error_file=$1
    if grep -Eqi 'manifest unknown|name unknown' "$error_file"; then
        printf 'manifest unknown'
    elif grep -Eqi 'requested access.*denied|unauthorized|authentication required' "$error_file"; then
        printf 'registry authentication denied'
    elif grep -Eqi 'too many requests|(^|[^0-9])429([^0-9]|$)' "$error_file"; then
        printf 'registry rate limited'
    elif grep -Eqi 'timed out|timeout' "$error_file"; then
        printf 'registry/network timeout'
    else
        tail -n 1 "$error_file"
    fi
}

run_with_retry() {
    operation=$1
    shift
    attempt=1
    while [ "$attempt" -le "$max_attempts" ]; do
        attempt_log="$work_dir/${operation}-${attempt}.log"
        echo "$operation attempt $attempt/$max_attempts for $source:$image_version"
        "$@" > "$attempt_log" 2>&1
        rc=$?
        cat "$attempt_log"
        if [ "$rc" -eq 0 ]; then
            return 0
        fi
        if is_permanent_error "$attempt_log"; then
            return "$rc"
        fi
        if ! is_transient_error "$attempt_log" || [ "$attempt" -eq "$max_attempts" ]; then
            return "$rc"
        fi
        sleep_seconds=$((2 ** (attempt - 1)))
        echo "Transient $operation failure; retrying in ${sleep_seconds}s"
        sleep "$sleep_seconds"
        attempt=$((attempt + 1))
    done
    return 1
}

write_status RUNNING "" "array ${array_job_id}_${task_id} running"

if command -v apptainer >/dev/null 2>&1; then
    container_runtime=apptainer
elif command -v singularity >/dev/null 2>&1; then
    container_runtime=singularity
else
    fail_task 69 "Apptainer or Singularity is required on the compute node"
fi

# A rerun can race with another initializer; only trust a SIF that inspection accepts.
if [ -s "$destination" ] && \
        "$container_runtime" inspect "$destination" >/dev/null 2>&1; then
    final_state=READY
    write_status READY 0 "existing SIF validated"
    exit 0
fi

mkdir -p -- "$work_parent"
work_dir=$(mktemp -d "${work_parent%/}/biomero-pull-${array_job_id}_${task_id}.XXXXXX") || \
    fail_task 73 "unable to create node-local temporary directory"
local_sif="$work_dir/image.sif"
export APPTAINER_TMPDIR="$work_dir/tmp"
export SINGULARITY_TMPDIR="$work_dir/tmp"
cache_parent=${SLURM_TMPDIR:-${configured_cachedir:-$work_dir}}
task_cache="${cache_parent%/}/biomero-cache-${array_job_id}_${task_id}"
export APPTAINER_CACHEDIR="$task_cache"
export SINGULARITY_CACHEDIR="$task_cache"
mkdir -p -- "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"

if [ "$source_type" = "registry" ]; then
    registry_ref="docker://${source}:${image_version}"
    # Native registry resolution is the portable preflight. The runtime reads
    # the OCI manifest before downloading, extracting, and converting layers.
    run_with_retry build "$container_runtime" build --force --disable-cache \
        --mksquashfs-args "-processors ${SLURM_CPUS_PER_TASK:-${BIOMERO_PULL_CPUS:-8}}" \
        "$local_sif" "$registry_ref"
    rc=$?
else
    [ -r "$source" ] || fail_task 66 "converter definition is missing or unreadable"
    run_with_retry build "$container_runtime" build --force --disable-cache \
        --mksquashfs-args "-processors ${SLURM_CPUS_PER_TASK:-${BIOMERO_PULL_CPUS:-8}}" \
        "$local_sif" "$source"
    rc=$?
fi

if [ "$rc" -ne 0 ]; then
    fail_task "$rc" "$(concise_reason "$work_dir/build-${attempt}.log")"
fi

[ -s "$local_sif" ] || fail_task 65 "build returned success but produced an empty SIF"
"$container_runtime" inspect "$local_sif" >/dev/null 2>&1 || \
    fail_task 65 "built SIF failed Apptainer/Singularity inspection"

mkdir -p -- "$(dirname "$destination")"
cp -- "$local_sif" "$shared_partial" || fail_task 74 "copy to shared storage failed"
[ -s "$shared_partial" ] || fail_task 65 "shared temporary SIF is empty"
"$container_runtime" inspect "$shared_partial" >/dev/null 2>&1 || \
    fail_task 65 "shared temporary SIF failed Apptainer/Singularity inspection"
mv -- "$shared_partial" "$destination" || fail_task 74 "atomic SIF publish failed"

final_state=READY
write_status READY 0 "built and validated"
exit 0
