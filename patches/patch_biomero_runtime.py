"""
Compatibility patch for the BIOMERO version pinned by this deployment.

Why this exists:
- BIOMERO's remote unpack and result packaging commands assume the Slurm login
  node has `7z`. Snellius exposes `7za`, so exports appeared to succeed but
  left data/in empty, and result imports could fail after successful workflows.
  The fallback keeps the same behavior on systems with `7z` while supporting
  Snellius.
- `mkdir -p` makes retries idempotent after a partially created workflow
  directory.
- Workflows can expose a `use_gpu` parameter, but BIOMERO's config can only add
  static sbatch parameters and some upstream Slurm scripts hard-code GPU
  requests. This patch removes hard-coded GPU requests from freshly cloned job
  scripts, toggles Singularity `--nv` from USE_GPU, and injects Snellius GPU
  sbatch parameters only when a workflow sets `use_gpu=true`.
- `slurm_data_bind_path` is required on Snellius so Apptainer can see the same
  BIOMERO data path that jobs receive. A blank value used to be exported as
  APPTAINER_BINDPATH="", which can make Apptainer complain about `/ as sandbox
  is not authorized`; now it fails early with a clear config error.
- Snellius does not propagate ambient SSH shell environment variables into
  file-based `sbatch` jobs. Workflow submissions now write a per-job env file
  and patched Slurm job scripts source it before launching containers.
  This must be applied to both Git-cloned scripts and BIOMERO descriptor-
  generated scripts.
- The upstream slurm-scripts repository used here does not include BIOMERO job
  scripts for CellExpansion, Stardist5d or BIOMERO's generic conversion array script, while
  our config exposes those workflows and uses conversion. We add those scripts
  during Slurm script setup so exposed workflows have matching sbatch targets.

Remove this when upstream BIOMERO includes these compatibility fixes.
"""

from pathlib import Path
import site
import sys


PATCH_DIR = Path(__file__).resolve().parent / "patches"
REMOTE_CONVERSION_MARKER = 'CONVERT_JOB_ARRAY = ""'
REMOTE_GENERATED_JOBS_MARKER = "GENERATED_JOBS = {}"


def _replace_required(source: str, old: str, new: str, description: str) -> str:
    if old not in source:
        raise RuntimeError(f"Could not patch BIOMERO slurm_client.py: {description}")
    return source.replace(old, new)


def _replace_remote_marker(source: str, marker: str, replacement: str) -> str:
    if marker not in source:
        raise RuntimeError(f"Remote Slurm postprocess payload is missing marker: {marker}")
    return source.replace(marker, replacement, 1)


def _biomero_file(name: str) -> Path:
    # Locate BIOMERO inside the active container venv without importing it.
    # Importing can fail while the package is half-patched during image build.
    candidates = []
    for base in site.getsitepackages() + [site.getusersitepackages(), *sys.path]:
        if base:
            candidates.append(Path(base) / "biomero" / name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Could not locate biomero/{name}")


def _read_patch(path: str) -> str:
    return (PATCH_DIR / path).read_text(encoding="utf-8")


def _render_remote_script_patch() -> str:
    # Python code that runs on Snellius after cloning the upstream Slurm script
    # repository. It fills gaps and normalizes scripts before BIOMERO submits jobs.
    generated_jobs = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((PATCH_DIR / "jobs").glob("*.sh"))
        if path.name != "convert_job_array.sh"
    }
    source = _read_patch("remote_script_postprocess.py")
    source = _replace_remote_marker(
        source,
        REMOTE_CONVERSION_MARKER,
        f'CONVERT_JOB_ARRAY = {_read_patch("jobs/convert_job_array.sh")!r}',
    )
    source = _replace_remote_marker(
        source,
        REMOTE_GENERATED_JOBS_MARKER,
        f"GENERATED_JOBS = {generated_jobs!r}",
    )
    return source


def patch_slurm_client() -> None:
    path = _biomero_file("slurm_client.py")
    source = path.read_text(encoding="utf-8")
    remote_patch = _render_remote_script_patch()
    generated_job_helper = _read_patch("generated_job_postprocess.py")

    if "import shlex\n" not in source:
        source = source.replace("import os\n", "import os\nimport shlex\n")

    if "_nl_biomero_normalize_generated_job_script" not in source:
        source = _replace_required(
            source,
            "logger = logging.getLogger(__name__)\n\nclass SlurmJob:",
            f"logger = logging.getLogger(__name__)\n\n{generated_job_helper}\n\nclass SlurmJob:",
            "generated Slurm job helper insertion",
        )

    # Make fresh Slurm script setup self-healing: after BIOMERO clones the
    # upstream repository, run our Snellius script normalization step too.
    old_clone = """            cmd = 'git clone "$REPOSRC" "$LOCALREPO" 2> /dev/null'
            r = self.run_commands([cleanup_first, cmd], env)
"""
    new_clone = f'''            cmd = 'git clone "$REPOSRC" "$LOCALREPO" 2> /dev/null'
            patch_workflow_gpu = r"""python3 - "$LOCALREPO" <<'PY'
{remote_patch}
PY"""
            r = self.run_commands([cleanup_first, cmd, patch_workflow_gpu], env)
'''
    source = _replace_required(
        source,
        old_clone,
        new_clone,
        "Slurm script repository setup hook",
    )

    # The clone hook above normalizes scripts from slurm_script_repo. When
    # slurm_script_repo is empty BIOMERO generates scripts locally and uploads
    # them directly, so normalize the generated script before upload too.
    source = _replace_required(
        source,
        """            job_script = src.safe_substitute(substitutes)
        return job_script
""",
        """            job_script = src.safe_substitute(substitutes)
        job_script = _nl_biomero_normalize_generated_job_script(job_script)
        return job_script
""",
        "generated Slurm job script env-file loader",
    )

    # Make unpacking exported image ZIPs work on Snellius and on retries:
    # Snellius provides `7za`, and `mkdir -p` tolerates existing directories.
    source = _replace_required(
        source,
        'unzip_cmd = f"mkdir \\"{self.slurm_data_path}/{zipfile}\\" \\',
        'unzip_cmd = f"mkdir -p \\"{self.slurm_data_path}/{zipfile}\\" \\',
        "remote export directory creation",
    )
    source = _replace_required(
        source,
        '                    7z x -y -o\\"{self.slurm_data_path}/{zipfile}/data/in\\" \\',
        '                    ZIP_CMD=$(command -v 7z || command -v 7za) && \\"$ZIP_CMD\\" x -y -o\\"{self.slurm_data_path}/{zipfile}/data/in\\" \\',
        "remote export ZIP extraction",
    )
    source = _replace_required(
        source,
        '    _ZIP_CMD = "cd \\"{data_location}/data/out\\" && 7z a -y \\"{data_location}/{filename}.zip\\" -tzip ."',
        '    _ZIP_CMD = "cd \\"{data_location}/data/out\\" && ZIP_CMD=$(command -v 7z || command -v 7za) && \\"$ZIP_CMD\\" a -y \\"{data_location}/{filename}.zip\\" -tzip ."',
        "remote result ZIP packaging",
    )

    # Inject Snellius GPU sbatch options only when the workflow exposes and
    # passes `use_gpu=true`; otherwise keep CPU jobs on the default partition.
    source = _replace_required(
        source,
        """        job_params = self.slurm_model_jobs_params[workflow.lower()]
        # grab only the image name, not the group/creator
""",
        """        job_params = list(self.slurm_model_jobs_params[workflow.lower()])
        use_gpu_value = kwargs.get("use_gpu")
        use_gpu = str(use_gpu_value).lower() in ("true", "1", "yes", "y", "on")
        if use_gpu:
            gpu_partition = os.getenv("BIOMERO_GPU_PARTITION", "gpu_a100")
            gpu_gres = os.getenv("BIOMERO_GPU_GRES", "gpu:a100:1")
            if gpu_partition and not any(param.startswith(" --partition=") for param in job_params):
                job_params.append(f" --partition={gpu_partition}")
            if gpu_gres and not any(param.startswith(" --gres=") for param in job_params):
                job_params.append(f" --gres={gpu_gres}")
        # grab only the image name, not the group/creator
""",
        "per-workflow GPU sbatch parameters",
    )

    # Fail loudly when the required Snellius Apptainer bind path is missing.
    # Without this, workflows can run but fail to write/import results.
    source = _replace_required(
        source,
        """        if self.slurm_data_bind_path is not None:
            sbatch_env["APPTAINER_BINDPATH"] = f"\\"{self.slurm_data_bind_path}\\""
""",
        """        if not self.slurm_data_bind_path:
            raise ValueError("slurm_data_bind_path must be set so Apptainer can access BIOMERO data paths")
        sbatch_env["APPTAINER_BINDPATH"] = f"\\"{self.slurm_data_bind_path}\\""
""",
        "required Slurm data bind path",
    )
    source = _replace_required(
        source,
        """        if self.slurm_conversion_partition is not None:
            sbatch_env["CONVERSION_PARTITION"] = f"\\"{self.slurm_conversion_partition}\\""
""",
        """        if self.slurm_conversion_partition:
            sbatch_env["CONVERSION_PARTITION"] = f"\\"{self.slurm_conversion_partition}\\""
""",
        "optional conversion partition export",
    )
    source = _replace_required(
        source,
        """        workflow_env = self.workflow_params_to_envvars(**kwargs)
        env = {**sbatch_env, **workflow_env}

        email_param = "" if email is None else f" --mail-user={email}"
        time_param = "" if time is None else f" --time={time}"
        job_params.append(time_param)
        job_params.append(email_param)
        job_param = "".join(job_params)
        sbatch_cmd = f"sbatch{job_param} --output=omero-%j.log \\
            \\"{self.slurm_script_path}/{job_script}\\""

        return sbatch_cmd, env
""",
        """        workflow_env = self.workflow_params_to_envvars(**kwargs)
        env = {**sbatch_env, **workflow_env}
        env_file = f"{self.slurm_data_path}/{input_data}/biomero_job_env.sh"
        env_lines = "\\n".join(
            f"export {key}={shlex.quote(str(value).strip(chr(34)))}"
            for key, value in env.items()
        )
        write_env_cmd = (
            f"cat > {shlex.quote(env_file)} <<'BIOMERO_ENV'\\n"
            f"{env_lines}\\n"
            "BIOMERO_ENV\\n"
        )

        email_param = "" if email is None else f" --mail-user={email}"
        time_param = "" if time is None else f" --time={time}"
        job_params.append(time_param)
        job_params.append(email_param)
        job_param = "".join(job_params)
        sbatch_cmd = f"{write_env_cmd}sbatch{job_param} --output=omero-%j.log \\
            \\"{self.slurm_script_path}/{job_script}\\" {shlex.quote(env_file)}"

        return sbatch_cmd, {}
""",
        "per-job Slurm environment file submission",
    )
    path.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    patch_slurm_client()
