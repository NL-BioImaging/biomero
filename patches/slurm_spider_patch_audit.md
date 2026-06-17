# BIOMERO Slurm Patch Audit

This branch separates generally useful BIOMERO Slurm extensions from Spider-
specific deployment policy.

## Generally useful extensions

- `7z`/`7za` fallback and idempotent remote unpack directories.
- Required `slurm_data_bind_path` validation before submitting container jobs.
- Per-job environment files for `sbatch` scripts, because remote Slurm jobs may
  not inherit SSH session environment variables.
- Normalizing cloned and descriptor-generated job scripts so hard-coded
  `singularity run --nv` becomes conditional on `USE_GPU`.
- Removing hard-coded `#SBATCH --gres` from cloned workflow scripts so GPU
  resources are controlled centrally.
- Adding missing local job scripts for exposed workflows that upstream
  `slurm-scripts` does not ship.
- Running image pulls/builds as foreground Slurm jobs with project-local
  Apptainer cache/temp directories, bounded CPU/memory, and real exit codes.
- Verifying workflow output directories before BIOMERO enters import, so
  container tracebacks or empty outputs fail early instead of hanging at 90%.
- Focused per-workflow CPU/memory/time defaults in `slurm-config.ini`.

## Spider-specific policy

- SSH/rendering variables use `SPIDER_USER` and `SPIDER_PROJECT`.
- Runtime Slurm paths point at `/project/surfadvisors/Share/biomero/...`.
- GPU-native workflows default to `use_gpu=true` through
  `BIOMERO_FORCE_GPU_WORKFLOWS`.
- Effective GPU jobs request `--partition=${BIOMERO_GPU_PARTITION}` and
  `--gpus=${BIOMERO_GPUS}`.
- CPU-only workflows, conversion jobs, and image-pull jobs omit `--partition` so
  Spider routes them to the normal/default partition.
- `slurm_conversion_partition` is blank for Spider.

## Removed cleanup scaffolding

- Removed migration code that rewrote the temporary inline
  `_nl_biomero_verify_outputs` helper and the temporary bad
  `$(dirname "$0")/biomero_job_helpers.sh` source path. Live Spider scripts and
  generated scripts now use the final helper path directly.
- Renamed an internal remote patch variable from a GPU-specific name to a
  generic Slurm script normalization name.

## Still intentionally deployment-specific

- The current runtime patch remains a compatibility shim for the pinned BIOMERO
  version. It should be retired when upstream BIOMERO supports these Slurm
  behaviors directly.
- StarDist/Cellpose channel limitations are workflow-container behavior, not
  Slurm integration behavior. The integration now surfaces those failures
  cleanly.