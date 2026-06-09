def _nl_biomero_normalize_generated_job_script(job_script: str) -> str:
    """Normalize BIOMERO descriptor-generated Slurm scripts for Snellius.

    This helper is injected into ``biomero.slurm_client`` and handles the path
    where BIOMERO builds a Slurm script directly from workflow descriptors
    instead of cloning ``slurm_script_repo``. Keep it small: the repository-clone
    path has its own self-contained remote payload in
    ``patches/remote_script_postprocess.py``.
    """
    if "BIOMERO_ENV_FILE" not in job_script:
        env_loader = (
            "\n"
            'BIOMERO_ENV_FILE="${1:-}"\n'
            'if [ -n "$BIOMERO_ENV_FILE" ] && [ -f "$BIOMERO_ENV_FILE" ]; then\n'
            '    . "$BIOMERO_ENV_FILE"\n'
            "fi\n"
        )
        lines = job_script.splitlines(keepends=True)
        insert_at = 0
        for idx, line in enumerate(lines):
            if line.startswith("#SBATCH"):
                insert_at = idx + 1
        lines.insert(insert_at, env_loader)
        job_script = "".join(lines)

    if "GPU_FLAG=" not in job_script:
        singularity_call = "singularity run --nv "
        if singularity_call in job_script:
            gpu_flag = (
                'GPU_FLAG=""\n'
                'case "${USE_GPU:-}" in\n'
                '  true|True|TRUE|1|yes|Yes|YES|y|Y|on|On|ON) GPU_FLAG="--nv" ;;\n'
                "esac\n\n"
            )
            job_script = job_script.replace(
                singularity_call,
                gpu_flag + "singularity run $GPU_FLAG ",
                1,
            )

    return job_script
