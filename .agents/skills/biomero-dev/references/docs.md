# Documentation

## Where settings are documented

Whenever you add, migrate, or change the meaning/scope of an ini setting, update
**every** user-facing surface below in the same change so they never drift. Do
not stop after editing the code — these are part of "done":

- `resources/slurm-config.ini`: the sample config shipped with the library. Add
  a commented example line under `[SLURM]` (or `[MODELS]` for per-workflow keys)
  showing the default and a realistic value.
- `docs/slurm-configuration.rst`: prose describing the option, its default, the
  ini key, and the environment-variable override.
- `docs/configuration-reference.rst`: the reference table/list of options, if the
  setting belongs there.
- **Deployment inis** outside this repo that ship real configs, e.g.
  `d:\workspace\NL-BIOMERO\web\slurm-config.ini`. Update the matching comment
  there too (note: deployment inis often use `key = value` spacing, unlike the
  `key=value` sample in `resources/`).
- **OMERO.biomero admin UI** —
  `d:\workspace\OMERO.biomero\webapp\src\biomero\components\SettingsForm.js`.
  If the setting is editable from the admin UI, update its field helper/
  description (and add the field if it is missing). Keep the UI explanation
  consistent with the ini comment and the rst prose: same default, same env var
  (`<EnvVarNote vars={[...]} />`), same scope/precedence wording. If you broaden
  a setting's scope (e.g. "applies to workflow **and** conversion jobs"), reflect
  that in the field text too.

The `__init__` docstring in `biomero/slurm_client.py` is the API-level source of
truth and feeds the autodoc pages, so write it carefully (default + env var).

## Cross-surface consistency checklist

Before considering an ini-setting change complete, confirm all of these read the
same story (default, env var, scope, precedence):

1. `__init__` docstring in `biomero/slurm_client.py`
2. `resources/slurm-config.ini` sample
3. `docs/slurm-configuration.rst`
4. `docs/configuration-reference.rst`
5. deployment inis (e.g. `NL-BIOMERO/web/slurm-config.ini`)
6. OMERO.biomero `SettingsForm.js` field (if admin-editable)
7. `biomero/constants.py` `slurm_env` — if the setting has an env var, its
   constant must exist there and be referenced by name.

## Building the docs

From the repo root with the bundled venv (set cwd first):

```powershell
Set-Location d:\workspace\biomero-clone
.\venvTest\Scripts\python.exe -m sphinx -b html docs docs\_build
```

Output lands in `docs/_build`. Check the build for warnings about missing
references or malformed RST in the files you touched.
