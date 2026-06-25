# BIOMERO Patch Removal Checklist

This file is a working checklist for upstreaming the behavior currently covered by the local runtime patches in `patches/` so those patches can be removed.

**Status: COMPLETE.** All runtime patch files have been removed.
- `patch_biomero_runtime.py` — deleted; all behaviors either upstreamed into core BIOMERO or intentionally excluded (see section 14)
- `patch_biomero_web_runtime.py` — deleted earlier; fix applied to OMERO.biomero surf branch directly

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

- [x] Add or refactor a shared internal helper for reading config values with precedence rules.
  Notes:
  - Goal is to avoid duplicating the same `default -> ini -> env override` logic in multiple places.
  - This likely applies not only to new options, but also to existing options like `sacct_start_time` / `sacct_days_ago`.
  - Helper should support at least: string, boolean, integer, optional-empty-as-None, and possibly custom parsers.
  - Helper should make precedence explicit and testable.

- [x] Decide and document one consistent precedence model for cluster-specific options.
  Proposed pattern:
  - code default
  - `slurm-config.ini`
  - environment variable override
  - explicit method argument still wins when applicable

- [x] Review existing `SACCT_START_TIME` implementation and align newer options with the same pattern.
  Current implementation anchor:
  - `biomero/slurm_client.py`, `get_jobs_info_command()`

- [x] Add tests for the shared helper once introduced.
  Include:
  - default only
  - ini only
  - env only
  - ini + env where env wins
  - invalid values falling back safely where appropriate
  Note:
  - Current unit suite is green at `314 passed`.
  - For future test changes in this area, prefer reusing existing fixtures instead
    of adding more narrowly scoped fixture variants unless there is a clear payoff.

## 2. SACCT Window Support

Status:
- Already implemented in code.
- Already partially covered by tests.
- Still needs review as part of the broader patch-removal work.

- [x] Review the current `sacct_start_time` / `sacct_days_ago` implementation for consistency with the intended extension pattern.

- [x] Review the current test coverage for precedence and keep it green.
  Existing tests already cover:
  - ini absolute date
  - ini relative days
  - env absolute override
  - env relative override
  - explicit arg override

- [x] Confirm docs and sample config match the actual precedence behavior.

## 3. ZIP / UNZIP Command Abstraction (`7z` vs `7za`)

Patch intent:
- Support clusters with `7za` instead of `7z`.
- Preserve default behavior on clusters that already have `7z`.
- Allow explicit command override in config.

Status:
- Largely implemented already.

- [x] Review `_zip_shell_cmd`, `get_zip_command()`, and `get_unzip_command()` for completeness.
  Current implementation anchors:
  - `_zip_shell_cmd` property in `biomero/slurm_client.py`
  - `get_zip_command()` in `biomero/slurm_client.py`
  - `get_unzip_command()` in `biomero/slurm_client.py`

- [x] Confirm every zip/unzip call site uses the helper instead of embedding `7z` directly.

- [x] Keep `mkdir -p` idempotent extraction behavior as the canonical implementation.

- [x] Review and keep tests for:
  - auto-detect expression
  - explicit `slurm_zip_cmd`
  - unzip command assembly
  - zip command assembly

- [x] Update README language from generic `7zip installed` to reflect actual supported behavior (`7z` or `7za`, optional explicit override).

## 4. Env-File Submission For Workflow Jobs

Patch intent:
- Some clusters do not propagate SSH session environment variables into `sbatch` jobs.
- Optional mode writes workflow env vars to a per-job file.
- Job script receives that file as `$1` and sources it.
- Must remain off by default.

Status:
- Implemented in core for generated scripts and workflow command generation.
- Test coverage exists for generated template injection and submission command generation.
- Remaining work is review, docs alignment, and explicit support policy.

Open design concern:
- This may not need runtime script injection at all.
- For clusters where env propagation into `sbatch` is known at init/config time,
  a cleaner design may be to deploy an env-file-enabled job template variant
  during `setup_slurm()` / `update_slurm_scripts()` instead of mutating the
  generated script text at runtime.
- Keep runtime behavior only if there is a clear need for per-submission
  switching on a stable cluster setup.

- [x] Review `env_file_submission` config parsing in `SlurmClient.from_config()`.

- [x] Review generated-script env-file injection helper.
  Current implementation anchor:
  - `_inject_env_file_sourcing()` in `biomero/slurm_client.py`

- [x] Decide whether `env_file_submission` should be a deploy-time template
  selection instead of a runtime script mutation.
  Preferred direction to evaluate first:
  - generate/deploy the correct job template during Slurm init / script update
  - keep command-side env-file writing in `get_workflow_command()` only when
    the deployed template expects it
  - avoid runtime patch-style text injection unless a concrete cluster/runtime
    use case requires per-job switching

- [x] Review generated-script use in `generate_slurm_job_for_workflow()`.

- [x] Review `get_workflow_command()` env-file submission path.
  Verify:
  - env file path location
  - quoting
  - argument passing to `sbatch`
  - returned env dict behavior

- [x] Decide and document the relationship between:
  - `inline_ssh_env`
  - `env_file_submission`
  Notes:
  - default backward-compatible behavior should remain unchanged -> inline defaults to true, env_file false
  - `env_file_submission=false` should continue to preserve existing behavior -> yes
  - when both are conceptually available, docs should explain which transport is used -> env_file wins. 
  Part of documentation perhaps.

- [x] Add or review tests for values that need shell quoting in env-file mode.
  Include examples with:
  - spaces
  - quotes
  - hyphens
  - booleans
  - numeric values
  Status note:
  - current tests cover the env-file submission path and basic command generation
  - targeted quoting edge-case coverage still looks worth adding

- [x] Verify whether conversion jobs also need an env-file-based path, or whether current conversion handling is sufficient.
  - Seems needed to me. Added the new features gated by env_file_submission. Writes also BIOMERO_ENV_FILE stuff into the $OPTIONAL_ENV of the script on setup.
  - On runtime at get_conversion_command, it also puts the envs into a file and provides that as part of the command - instead of giving the env to connection. gated by env_file_submission.
  - Should have backwards compatible equal old behavior.

- [x] Document `env_file_submission` in the main docs, not only the sample ini.

## 5. Generated Script Post-Processing For GPU Flag Injection

Patch intent:
- Descriptor-generated job scripts may contain `singularity run --nv`.
- Optional mode should rewrite that to a `USE_GPU`-gated flag.
- Must remain off by default.

Status:
- Implemented in core for generated scripts and submission-time GPU flag substitution.
- Test coverage exists for generated template behavior and workflow command GPU handling.

Open design concern:
- Unlike env-file sourcing, GPU handling has a stronger runtime case because
  users may toggle `use_gpu` per workflow submission.
- Even so, prefer evaluating a template-driven approach first
  (for example, a template path that already uses `$GPU_FLAG` / `USE_GPU`)
  before keeping runtime text rewriting as the long-term design.
- Current implementation direction:
  - template-side runtime bash logic was removed
  - `$GPU_FLAG` is now intended to be decided by BIOMERO during generation /
    submission-time substitution
  - one remaining TODO is to finish the BIOMERO-side substitution path so
    `inject_gpu_flag` can map to `GPU_FLAG=""` by default and later to
    user-gated runtime behavior via `USE_GPU`

- [x] Review `inject_gpu_flag` config parsing in `SlurmClient.from_config()`.

- [x] Review generated-script GPU normalization helper.
  Current implementation anchor:
  - `_make_gpu_flag_conditional()` in `biomero/slurm_client.py`
  Note:
  - the old helper-based test dependency was removed from the active path
  - current coverage should target generated-template behavior instead of
    importing the removed helper directly

- [x] Decide whether GPU support should remain runtime text rewriting or move
  to a deployed template variant with built-in `USE_GPU` handling.
  Notes:
  - runtime need is more defensible here than for env-file sourcing
  - still prefer template-based deployment if it keeps per-run GPU toggling
    without patch-style mutation

- [x] Review generated-script use in `generate_slurm_job_for_workflow()`.

- [x] Complete BIOMERO-side `GPU_FLAG` substitution logic.
  Desired direction:
  - backward-compatible default remains `GPU_FLAG="--nv"`
  - when the dynamic GPU feature is enabled, BIOMERO should substitute
    `GPU_FLAG` based on runtime workflow intent instead of relying on bash
    logic embedded in the template
  - the likely anchor for this is `get_workflow_command()` / the normal
    workflow submission path, not a template self-check

- [x] Confirm behavior is idempotent and only affects generated scripts when enabled.

- [x] Keep default fully backward compatible when `inject_gpu_flag=false`.

- [x] Review tests for:
  - replacement of `--nv`
  - unchanged scripts when `--nv` absent
  - idempotence
  - interaction with env-file sourcing when both flags are enabled
  Status note:
  - current suite covers generated-template replacement and default `--nv` fallback
  - direct coverage for every listed edge case should still be re-checked if behavior changes again

- [x] Document `inject_gpu_flag` in the main docs, not only the sample ini.

- [x] Evaluate collapsing the generated workflow templates into a single
  BIOMERO-owned template with substitutions for workflow family differences.
  Candidate substitutions:
  - `GPU_FLAG`
  - `INPARAMS`
  - `OUTPARAMS`
  - `PARAMS`
  - BIAFLOWS-only fixed args such as `--local` and `-nmc`
  Goal:
  - avoid maintaining four near-duplicate templates
  - keep the generated sbatch scripts fully testable and inspectable
  - keep the logic in BIOMERO generation/substitution, not as runtime patching
  Status note:
  - implemented with one shared `job_template.sh`
  - env-file handling is injected via `OPTIONAL_ENV`
  - workflow-family differences are injected via `WF_TYPE`, `INPARAMS`, `OUTPARAMS`, and `EXTRAPARAMS`
  - narrow test slice covering shared-template generation passed (`5 passed`)

## 6. Dynamic GPU SBATCH Defaults When `use_gpu=true`

Patch intent:
- Allow workflows to expose `use_gpu` without forcing GPU resource requests in all cases.
- Add fallback `--partition` and `--gres` only when:
  - workflow submits `use_gpu=true`
  - fallback GPU defaults are configured
  - per-workflow job params do not already define them

Status:
- Implemented in core with fallback injection gated by `use_gpu=true`.
- Canonical BIOMERO-prefixed env var overrides are wired through the shared config helper.

- [x] Review `get_workflow_command()` GPU fallback injection logic.

- [x] Confirm precedence remains:
  - explicit per-workflow `*_job_partition` / `*_job_gres`
  - then fallback GPU defaults
  - and only when `use_gpu=true`

- [x] Decide and standardize environment variable naming for GPU fallback overrides.
  Current mismatch:
  - sample patch docstring refers to `BIOMERO_GPU_PARTITION` / `BIOMERO_GPU_GRES`
  - current implementation uses `GPU_PARTITION` / `GPU_GRES`
  Recommendation:
  - support `BIOMERO_GPU_PARTITION` / `BIOMERO_GPU_GRES` as canonical names
  - optionally accept old names too if already shipped
  - document precedence if both exist

- [x] Refactor GPU env override parsing through the same shared config helper as other options.

- [x] Review tests for:
  - `use_gpu=True` adds fallback params
  - `use_gpu=False` does not add them
  - per-workflow params suppress duplicate fallback params

- [x] Document fallback GPU behavior and clarify that it is opt-in and backward compatible.

## 7. `slurm_data_bind_path` Behavior

Patch intent:
- Snellius requires `APPTAINER_BINDPATH`.
- Local patch forced an error when unset.

Upstream recommendation:
- Keep this optional in core BIOMERO.
- Do not make it globally required.

Status:
- Current upstream-started implementation keeps it optional, which is the correct backward-compatible behavior.

- [x] Keep `slurm_data_bind_path` optional in core BIOMERO.

- [x] Review where `APPTAINER_BINDPATH` is exported for workflow and conversion commands.

- [x] Review tests covering:
  - unset bind path: no error, no env var
  - configured bind path: env var added

- [x] Improve docs to clearly state:
  - when it is needed
  - that leaving it unset is valid on clusters where default container bind behavior is sufficient

- [x] Do not upstream the patch behavior that raises a `ValueError` globally when it is missing.

## 8. `slurm_conversion_partition`

Patch intent:
- Some clusters require an explicit conversion partition.
- This must remain optional.

Status:
- Started in current code.

- [x] Review config parsing for `slurm_conversion_partition`.

- [x] Review conversion environment export in `get_conversion_command()`.

- [x] Confirm behavior when unset is unchanged from historical default.

- [x] Add or review tests for set vs unset behavior.

- [x] Document this in the main docs and sample config consistently.

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

- [x] Decide whether cloned external scripts are:
  - an external contract that must already be compatible, 

- [x] If normalization is supported, define a minimal, opt-in, well-documented policy.
  Constraints:
  - nope

- [x] If normalization is not supported, document that:
  - `slurm_script_repo` contents must already support required behavior
  - external script repo versioning is the supported mechanism
  - this should already be documented for that option. nobody should use that option unless they know better than us

- [x] Review `setup_job_scripts()` and align implementation with the chosen policy.
  - just a clone or we do our jobs

## 10. Missing External Job Scripts: Explicit Non-Goal For Core BIOMERO

Patch behavior that should stay out of core BIOMERO:
- adding missing `convert_job_array.sh`
- adding missing workflow job scripts such as CellExpansion variants

- [x] Keep this out of upstream `biomero`.

- [x] Document this as an external repository responsibility.
  - it's just part of switching to a repo of scripts -> then its not our responsbility. 

- [x] If needed, create follow-up work outside this repo for updating the external `slurm-scripts` repository.
  - nah, out of date, intiial mvp, will not update.

## 11. Hard-Coded GPU Directives In External Scripts

Patch behavior:
- strips `#SBATCH --gres=...` from cloned scripts
- rewrites `singularity run --nv`

This is potentially too invasive for default upstream behavior.

- [x] Decide whether BIOMERO should ever rewrite external scripts' SBATCH directives.
  - no

- [x] Prefer handling this in the external script repository instead of in core BIOMERO where possible.
  - yes

- [x] If BIOMERO keeps any normalization for external scripts, limit it to clearly documented, opt-in transformations.
  - nothing. external scripts are on their own and not supported.

## 12. Docs Alignment

Status:
- `resources/slurm-config.ini` is more complete than the prose docs.
- Main docs and README still lag behind actual code in some places.

- [x] Expand `docs/slurm-configuration.rst` to cover all supported optional settings with the same pattern used for sacct window settings.
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

- [x] For each supported setting, document:
  - default behavior
  - `slurm-config.ini` key
  - environment variable override
  - precedence
  - backward compatibility expectations

- [x] Update README cluster requirements and setup text to match actual code behavior.

- [x] Make docs explicit about what is core BIOMERO behavior versus what belongs in external workflow script repos.

## 13. Tests And Validation Pass

Status:
- Many unit tests already exist for newer behavior.
- They should be treated as part of the patch-removal deliverable, not as optional extras.

- [x] Review existing new tests in `tests/unit/test_slurm_client.py` and keep them passing.

- [x] Add tests for any newly introduced shared config helper.

- [x] Add tests for any finalized environment variable naming decisions.

- [x] Add tests for doc/example alignment where practical via config parsing tests.

- [x] Run the narrow unit test slice for `slurm_client` after each implementation batch.

- [x] Run a broader repo test pass once the checklist items affecting public behavior are complete.

## 14. Cleanup Before Removing Patches

- [x] Compare each behavior in `patches/patch_biomero_runtime.py` with upstream code and mark it as one of:
  - implemented in core
  - implemented but still needs docs/tests/review
  - intentionally rejected for core
  - external repo responsibility

  Review summary:
  - implemented in core:
    - shared config/env precedence helper
    - sacct window support
    - zip/unzip command abstraction
    - env-file submission for workflow and conversion jobs
    - generated-script GPU flag handling
    - shared GPU fallback params with configurable resource flag
    - optional `slurm_data_bind_path`
    - optional `slurm_conversion_partition`
    - apptainer tmp/cache env support for image pulls
    - optional sbatch-based image pull/build path
    - job template output verification
  - intentionally rejected for core:
    - forcing `slurm_data_bind_path` globally
    - mutating cloned external script repositories
    - adding/removing hard-coded GPU directives in external scripts
    - adding missing external repo job scripts into core behavior
  - external repo / deployment responsibility:
    - custom `slurm_script_repo` contents
  - applied upstream to OMERO.biomero surf branch:
    - Metabase localhost/127.0.0.1 link rewrite (`BiomeroApp.js`)

- [x] Remove obsolete Slurm runtime patches after the above comparison is complete.
  All runtime patch files removed:
  - `patch_biomero_web_runtime.py` — deleted; fix applied to OMERO.biomero surf branch directly
  - `metabase_link_old.js` — deleted
  - `metabase_link_new.js` — deleted
  - `patch_biomero_runtime.py` — deleted; all behaviors upstreamed into core BIOMERO or superseded:
    - `BIOMERO_FORCE_GPU_WORKFLOWS` env var logic → superseded by `<name>_use_gpu=true` in `[MODELS]` and the `gpu_gres`/`gpu_gpus` shared fallback config
    - `_nl_biomero_normalize_generated_job_script` hookup → superseded by behaviors built directly into `job_template.sh` (`set -eo pipefail`, `$OPTIONAL_ENV`, `$GPU_FLAG`)
    - required `slurm_data_bind_path` → intentionally kept optional in core (cluster-specific requirement stays in deployment config)
    - all other behaviors (zip/unzip, env-file submission, GPU fallback params, sbatch-based image pulls, apptainer tmp/cache) → implemented in core BIOMERO
  - `patches/` directory — removed when patch source files were upstreamed

- [x] Ensure release notes / PR summary clearly state:
  - what behavior moved into core BIOMERO
  - what remains opt-in
  - what remains external-repo responsibility
  - what backward-compatible defaults remain unchanged

  PR summary notes:
  - moved into core BIOMERO: config/env precedence helper, env-file submission, GPU fallback handling, zip command abstraction, optional conversion/bind settings, sbatch-based image pulling, apptainer tmp/cache settings, and output verification in generated job templates
  - remains opt-in: env-file submission, inject_gpu_flag, shared GPU defaults, sbatch-based image pulling, apptainer tmp/cache overrides
  - remains external responsibility: custom script repositories
  - applied to OMERO.biomero surf branch: Metabase localhost link rewrite (no longer a deployment patch)
  - backward-compatible defaults remain unchanged unless the new options are explicitly enabled

## 15. Quick Status Snapshot

Likely already implemented but must still be reviewed and treated as real deliverables:

- [x] `sacct_start_time` / `sacct_days_ago`
- [x] `slurm_zip_cmd`
- [x] `7z` / `7za` auto-detection
- [x] idempotent unzip directory creation with `mkdir -p`
- [x] `env_file_submission` config parsing
- [x] generated-script env-file sourcing helper
- [x] `inject_gpu_flag` config parsing
- [x] generated-script GPU flag helper
- [x] fallback GPU parameter injection in `get_workflow_command()`
- [x] `slurm_data_bind_path` optional export
- [x] `slurm_conversion_partition` optional export

Still genuinely unresolved and requiring design or policy work:

- [x] shared config/env precedence helper
- [x] canonical GPU env var naming
- [x] official policy for post-processing cloned external job scripts
- [x] explicit non-goal documentation for missing external script injection
- [x] docs alignment across README, sample config, and prose docs

## 16. Suggested Implementation Order

- [x] Step 1: Introduce shared config/env precedence helper and refactor sacct + newer options onto it.
- [x] Step 2: Finalize and test canonical env var names, especially GPU-related ones.
- [x] Step 3: Review and harden env-file submission path for generated scripts and workflow submission.
- [x] Step 4: Review and harden generated-script GPU flag injection.
- [x] Step 5: Complete docs for all supported optional settings.
- [x] Step 6: Decide policy for cloned external script normalization.
- [x] Step 7: Explicitly document external-repo responsibilities and non-goals.
- [x] Step 8: Reconcile patch behavior vs core behavior and remove obsolete patches.
