# BIOMERO Patch Removal Checklist

This file is a working checklist for upstreaming the behavior currently covered by the local runtime patches in `patches/` so those patches can be removed.

Scope:
- Focus on behavior currently patched in this clone of `biomero`.
- Keep all changes backward compatible by default.
- Follow the existing `SACCT_START_TIME` pattern where relevant:
  - stable default behavior
  - optional `slurm-config.ini` setting
  - optional environment variable override
  - explicit precedence documented and tested

Out of scope:
- Do not implement missing external Slurm job scripts inside `biomero`.
- Do not make Snellius-specific requirements global defaults.
- Do not force cluster-specific behavior on all users.

## How to use this file

- Check items off as code, tests, and docs are completed.
- Keep links and notes updated if implementation moves.
- This file is intended to work both as:
  - a future-session implementation guide
  - a PR summary / review checklist

## Sources reviewed

- `patches/patch_biomero_runtime.py`
- `patches/generated_script_postprocess.py`
- `patches/remote_script_postprocess.py`
- `resources/slurm-config.ini`
- `biomero/slurm_client.py`
- `tests/unit/test_slurm_client.py`
- `docs/slurm-configuration.rst`
- `README.md`

## 1. Configuration Pattern And Precedence

- [ ] Add or refactor a shared internal helper for reading config values with precedence rules.
  Notes:
  - Goal is to avoid duplicating the same `default -> ini -> env override` logic in multiple places.
  - This likely applies not only to new options, but also to existing options like `sacct_start_time` / `sacct_days_ago`.
  - Helper should support at least: string, boolean, integer, optional-empty-as-None, and possibly custom parsers.
  - Helper should make precedence explicit and testable.

- [ ] Decide and document one consistent precedence model for cluster-specific options.
  Proposed pattern:
  - code default
  - `slurm-config.ini`
  - environment variable override
  - explicit method argument still wins when applicable

- [ ] Review existing `SACCT_START_TIME` implementation and align newer options with the same pattern.
  Current implementation anchor:
  - `biomero/slurm_client.py`, `get_jobs_info_command()`

- [ ] Add tests for the shared helper once introduced.
  Include:
  - default only
  - ini only
  - env only
  - ini + env where env wins
  - invalid values falling back safely where appropriate

## 2. SACCT Window Support

Status:
- Already implemented in code.
- Already partially covered by tests.
- Still needs review as part of the broader patch-removal work.

- [ ] Review the current `sacct_start_time` / `sacct_days_ago` implementation for consistency with the intended extension pattern.

- [ ] Review the current test coverage for precedence and keep it green.
  Existing tests already cover:
  - ini absolute date
  - ini relative days
  - env absolute override
  - env relative override
  - explicit arg override

- [ ] Confirm docs and sample config match the actual precedence behavior.

## 3. ZIP / UNZIP Command Abstraction (`7z` vs `7za`)

Patch intent:
- Support clusters with `7za` instead of `7z`.
- Preserve default behavior on clusters that already have `7z`.
- Allow explicit command override in config.

Status:
- Largely implemented already.

- [ ] Review `_zip_shell_cmd`, `get_zip_command()`, and `get_unzip_command()` for completeness.
  Current implementation anchors:
  - `_zip_shell_cmd` property in `biomero/slurm_client.py`
  - `get_zip_command()` in `biomero/slurm_client.py`
  - `get_unzip_command()` in `biomero/slurm_client.py`

- [ ] Confirm every zip/unzip call site uses the helper instead of embedding `7z` directly.

- [ ] Keep `mkdir -p` idempotent extraction behavior as the canonical implementation.

- [ ] Review and keep tests for:
  - auto-detect expression
  - explicit `slurm_zip_cmd`
  - unzip command assembly
  - zip command assembly

- [ ] Update README language from generic `7zip installed` to reflect actual supported behavior (`7z` or `7za`, optional explicit override).

## 4. Env-File Submission For Workflow Jobs

Patch intent:
- Some clusters do not propagate SSH session environment variables into `sbatch` jobs.
- Optional mode writes workflow env vars to a per-job file.
- Job script receives that file as `$1` and sources it.
- Must remain off by default.

Status:
- Started and mostly implemented for generated scripts and workflow command generation.
- Needs upstream hardening, review, documentation, and explicit support policy.

- [ ] Review `env_file_submission` config parsing in `SlurmClient.from_config()`.

- [ ] Review generated-script env-file injection helper.
  Current implementation anchor:
  - `_inject_env_file_sourcing()` in `biomero/slurm_client.py`

- [ ] Review generated-script use in `generate_slurm_job_for_workflow()`.

- [ ] Review `get_workflow_command()` env-file submission path.
  Verify:
  - env file path location
  - quoting
  - argument passing to `sbatch`
  - returned env dict behavior

- [ ] Decide and document the relationship between:
  - `inline_ssh_env`
  - `env_file_submission`
  Notes:
  - default backward-compatible behavior should remain unchanged
  - `env_file_submission=false` should continue to preserve existing behavior
  - when both are conceptually available, docs should explain which transport is used

- [ ] Add or review tests for values that need shell quoting in env-file mode.
  Include examples with:
  - spaces
  - quotes
  - hyphens
  - booleans
  - numeric values

- [ ] Verify whether conversion jobs also need an env-file-based path, or whether current conversion handling is sufficient.

- [ ] Document `env_file_submission` in the main docs, not only the sample ini.

## 5. Generated Script Post-Processing For GPU Flag Injection

Patch intent:
- Descriptor-generated job scripts may contain `singularity run --nv`.
- Optional mode should rewrite that to a `USE_GPU`-gated flag.
- Must remain off by default.

Status:
- Started and mostly implemented for generated scripts.

- [ ] Review `inject_gpu_flag` config parsing in `SlurmClient.from_config()`.

- [ ] Review generated-script GPU normalization helper.
  Current implementation anchor:
  - `_make_gpu_flag_conditional()` in `biomero/slurm_client.py`

- [ ] Review generated-script use in `generate_slurm_job_for_workflow()`.

- [ ] Confirm behavior is idempotent and only affects generated scripts when enabled.

- [ ] Keep default fully backward compatible when `inject_gpu_flag=false`.

- [ ] Review tests for:
  - replacement of `--nv`
  - unchanged scripts when `--nv` absent
  - idempotence
  - interaction with env-file sourcing when both flags are enabled

- [ ] Document `inject_gpu_flag` in the main docs, not only the sample ini.

## 6. Dynamic GPU SBATCH Defaults When `use_gpu=true`

Patch intent:
- Allow workflows to expose `use_gpu` without forcing GPU resource requests in all cases.
- Add fallback `--partition` and `--gres` only when:
  - workflow submits `use_gpu=true`
  - fallback GPU defaults are configured
  - per-workflow job params do not already define them

Status:
- Started and mostly implemented.

- [ ] Review `get_workflow_command()` GPU fallback injection logic.

- [ ] Confirm precedence remains:
  - explicit per-workflow `*_job_partition` / `*_job_gres`
  - then fallback GPU defaults
  - and only when `use_gpu=true`

- [ ] Decide and standardize environment variable naming for GPU fallback overrides.
  Current mismatch:
  - sample patch docstring refers to `BIOMERO_GPU_PARTITION` / `BIOMERO_GPU_GRES`
  - current implementation uses `GPU_PARTITION` / `GPU_GRES`
  Recommendation:
  - support `BIOMERO_GPU_PARTITION` / `BIOMERO_GPU_GRES` as canonical names
  - optionally accept old names too if already shipped
  - document precedence if both exist

- [ ] Refactor GPU env override parsing through the same shared config helper as other options.

- [ ] Review tests for:
  - `use_gpu=True` adds fallback params
  - `use_gpu=False` does not add them
  - per-workflow params suppress duplicate fallback params

- [ ] Document fallback GPU behavior and clarify that it is opt-in and backward compatible.

## 7. `slurm_data_bind_path` Behavior

Patch intent:
- Snellius requires `APPTAINER_BINDPATH`.
- Local patch forced an error when unset.

Upstream recommendation:
- Keep this optional in core BIOMERO.
- Do not make it globally required.

Status:
- Current upstream-started implementation keeps it optional, which is the correct backward-compatible behavior.

- [ ] Keep `slurm_data_bind_path` optional in core BIOMERO.

- [ ] Review where `APPTAINER_BINDPATH` is exported for workflow and conversion commands.

- [ ] Review tests covering:
  - unset bind path: no error, no env var
  - configured bind path: env var added

- [ ] Improve docs to clearly state:
  - when it is needed
  - that leaving it unset is valid on clusters where default container bind behavior is sufficient

- [ ] Do not upstream the patch behavior that raises a `ValueError` globally when it is missing.

## 8. `slurm_conversion_partition`

Patch intent:
- Some clusters require an explicit conversion partition.
- This must remain optional.

Status:
- Started in current code.

- [ ] Review config parsing for `slurm_conversion_partition`.

- [ ] Review conversion environment export in `get_conversion_command()`.

- [ ] Confirm behavior when unset is unchanged from historical default.

- [ ] Add or review tests for set vs unset behavior.

- [ ] Document this in the main docs and sample config consistently.

## 9. Policy For Repository-Provided Job Scripts (`slurm_script_repo`)

This is the biggest unresolved design item.

Patch behavior:
- After cloning the external script repo, a remote Python payload rewrites scripts to:
  - source env files
  - make GPU optional
  - strip hard-coded `#SBATCH --gres=...`
  - normalize a log line format

Upstream decision needed:
- Decide whether BIOMERO officially supports post-processing cloned external scripts.

- [ ] Decide whether cloned external scripts are:
  - an external contract that must already be compatible, or
  - something BIOMERO is allowed to normalize after clone

- [ ] If normalization is supported, define a minimal, opt-in, well-documented policy.
  Constraints:
  - should be off by default
  - should be idempotent
  - should not silently create missing scripts
  - should avoid deployment-specific assumptions where possible

- [ ] If normalization is not supported, document that:
  - `slurm_script_repo` contents must already support required behavior
  - external script repo versioning is the supported mechanism

- [ ] Review `setup_job_scripts()` and align implementation with the chosen policy.

## 10. Missing External Job Scripts: Explicit Non-Goal For Core BIOMERO

Patch behavior that should stay out of core BIOMERO:
- adding missing `convert_job_array.sh`
- adding missing workflow job scripts such as CellExpansion variants

- [ ] Keep this out of upstream `biomero`.

- [ ] Document this as an external repository responsibility.

- [ ] If needed, create follow-up work outside this repo for updating the external `slurm-scripts` repository.

## 11. Hard-Coded GPU Directives In External Scripts

Patch behavior:
- strips `#SBATCH --gres=...` from cloned scripts
- rewrites `singularity run --nv`

This is potentially too invasive for default upstream behavior.

- [ ] Decide whether BIOMERO should ever rewrite external scripts' SBATCH directives.

- [ ] Prefer handling this in the external script repository instead of in core BIOMERO where possible.

- [ ] If BIOMERO keeps any normalization for external scripts, limit it to clearly documented, opt-in transformations.

## 12. Docs Alignment

Status:
- `resources/slurm-config.ini` is more complete than the prose docs.
- Main docs and README still lag behind actual code in some places.

- [ ] Expand `docs/slurm-configuration.rst` to cover all supported optional settings with the same pattern used for sacct window settings.
  Add sections for:
  - `sacct_start_time`
  - `sacct_days_ago`
  - `env_file_submission`
  - `inject_gpu_flag`
  - `gpu_partition`
  - `gpu_gres`
  - `slurm_zip_cmd`
  - `slurm_data_bind_path`
  - `slurm_conversion_partition`

- [ ] For each supported setting, document:
  - default behavior
  - `slurm-config.ini` key
  - environment variable override
  - precedence
  - backward compatibility expectations

- [ ] Update README cluster requirements and setup text to match actual code behavior.

- [ ] Make docs explicit about what is core BIOMERO behavior versus what belongs in external workflow script repos.

## 13. Tests And Validation Pass

Status:
- Many unit tests already exist for newer behavior.
- They should be treated as part of the patch-removal deliverable, not as optional extras.

- [ ] Review existing new tests in `tests/unit/test_slurm_client.py` and keep them passing.

- [ ] Add tests for any newly introduced shared config helper.

- [ ] Add tests for any finalized environment variable naming decisions.

- [ ] Add tests for doc/example alignment where practical via config parsing tests.

- [ ] Run the narrow unit test slice for `slurm_client` after each implementation batch.

- [ ] Run a broader repo test pass once the checklist items affecting public behavior are complete.

## 14. Cleanup Before Removing Patches

- [ ] Compare each behavior in `patches/patch_biomero_runtime.py` with upstream code and mark it as one of:
  - implemented in core
  - implemented but still needs docs/tests/review
  - intentionally rejected for core
  - external repo responsibility

- [ ] Remove runtime patches only after the above comparison is complete.

- [ ] Ensure release notes / PR summary clearly state:
  - what behavior moved into core BIOMERO
  - what remains opt-in
  - what remains external-repo responsibility
  - what backward-compatible defaults remain unchanged

## 15. Quick Status Snapshot

Likely already implemented but must still be reviewed and treated as real deliverables:

- [ ] `sacct_start_time` / `sacct_days_ago`
- [ ] `slurm_zip_cmd`
- [ ] `7z` / `7za` auto-detection
- [ ] idempotent unzip directory creation with `mkdir -p`
- [ ] `env_file_submission` config parsing
- [ ] generated-script env-file sourcing helper
- [ ] `inject_gpu_flag` config parsing
- [ ] generated-script GPU flag helper
- [ ] fallback GPU parameter injection in `get_workflow_command()`
- [ ] `slurm_data_bind_path` optional export
- [ ] `slurm_conversion_partition` optional export

Still genuinely unresolved and requiring design or policy work:

- [ ] shared config/env precedence helper
- [ ] canonical GPU env var naming
- [ ] official policy for post-processing cloned external job scripts
- [ ] explicit non-goal documentation for missing external script injection
- [ ] docs alignment across README, sample config, and prose docs

## 16. Suggested Implementation Order

- [ ] Step 1: Introduce shared config/env precedence helper and refactor sacct + newer options onto it.
- [ ] Step 2: Finalize and test canonical env var names, especially GPU-related ones.
- [ ] Step 3: Review and harden env-file submission path for generated scripts and workflow submission.
- [ ] Step 4: Review and harden generated-script GPU flag injection.
- [ ] Step 5: Complete docs for all supported optional settings.
- [ ] Step 6: Decide policy for cloned external script normalization.
- [ ] Step 7: Explicitly document external-repo responsibilities and non-goals.
- [ ] Step 8: Reconcile patch behavior vs core behavior and remove obsolete patches.
