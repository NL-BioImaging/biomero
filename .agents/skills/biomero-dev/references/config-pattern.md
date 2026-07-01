# Config Option Pattern

## The precedence helper

All configurable settings resolve through the static helper on `SlurmClient`:

```python
_get_config_value(
    configs,            # configparser instance
    section="SLURM",
    option="<ini_key>",
    default=<code_default>,
    env_vars=[slurm_env.BIOMERO_<NAME>],  # first match wins; optional
    value_type=str,     # or int / bool
    empty_is_none=False,  # True => empty string parsed as None
)
```

Precedence is: **code default -> ini value -> environment variable** (the env
var, when present and non-empty, wins). Never reimplement this precedence with
raw `os.getenv` in `from_config()`; route everything through the helper so
behavior and logging stay uniform.

Env var name constants live in `biomero/constants.py` under `class slurm_env`.
Add the new constant there (e.g. `BIOMERO_DEFAULT_PARTITION = "BIOMERO_DEFAULT_PARTITION"`)
and reference it via `slurm_env.<NAME>` — do not inline raw strings.

## Touch points for adding / migrating an option

Use `slurm_conversion_partition` and `slurm_default_partition` as worked
examples. A new `[SLURM]` option requires edits in these places, in order:

1. `biomero/constants.py` — add the env var name to `class slurm_env`.
2. `biomero/slurm_client.py` `SlurmClient.__init__`:
   - add the keyword parameter (with a backward-compatible default, usually
     `None` or `False`);
   - add a docstring entry in the Args block describing default + the env var
     override;
   - store it: `self.<name> = <name>`.
3. `from_config()`:
   - parse it via `_get_config_value(...)` (place it next to a related option);
   - pass it into the `cls(...)` constructor call (mind the exact-kwargs guard in
     `test_from_config`).
4. The method that consumes it (e.g. `get_workflow_command()`): apply the value
   where appropriate. Respect ordering and precedence guards — only act when no
   more-specific value is already present (e.g.
   `not any(p.startswith(" --partition=") for p in job_params)`).
5. Tests in `tests/unit/test_slurm_client.py` (see references/testing.md).
6. Docs and config surfaces — keep ALL of these in sync (see references/docs.md):
   `resources/slurm-config.ini` sample, `docs/slurm-configuration.rst`,
   `docs/configuration-reference.rst`, any deployment ini (e.g.
   `NL-BIOMERO/web/slurm-config.ini`), and the OMERO.biomero admin UI
   (`OMERO.biomero/webapp/src/biomero/components/SettingsForm.js`) when the
   setting is editable there. If you change a setting's scope or precedence, the
   ini comments, rst prose, and UI field text must all say the same thing.

## Ordering / precedence guards

When a setting injects an sbatch flag, place the apply logic so that more
specific configuration wins:

- per-workflow params in `[WORKFLOWS]` (or `[MODELS]` for legacy) (already in `job_params`) win over global
  fallbacks;
- the GPU path (set by `inject_gpu_flag` / per-workflow `_use_gpu`) wins over a
  generic fallback partition;
- a generic fallback (e.g. `slurm_default_partition`) is appended last, and only
  when nothing more specific already set that flag.

Guard pattern:

```python
if self.<setting> and not any(
    p.startswith(" --<flag>=") for p in job_params
):
    job_params.append(f" --<flag>={self.<setting>}")
```

## Per-workflow GPU (no env needed)

Forcing GPU per workflow is config, not code: a `<name>_use_gpu=true` entry in
`[WORKFLOWS]`/`[MODELS]` sets `slurm_model_use_gpu[name]`, which `get_workflow_command()` uses
as the default for `use_gpu`. This is runtime-configurable via init-slurm and
does not require a container restart, unlike an environment variable. Prefer it
over env-var-driven force-GPU flags.
