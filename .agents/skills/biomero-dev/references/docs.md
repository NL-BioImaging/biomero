# Documentation

## Where settings are documented

A configurable setting is documented in three user-facing places — keep them in
sync with the code:

- `resources/slurm-config.ini`: the sample config shipped with the library. Add
  a commented example line under `[SLURM]` (or `[MODELS]` for per-workflow keys)
  showing the default and a realistic value.
- `docs/slurm-configuration.rst`: prose describing the option, its default, the
  ini key, and the environment-variable override.
- `docs/configuration-reference.rst`: the reference table/list of options, if the
  setting belongs there.

The `__init__` docstring in `biomero/slurm_client.py` is the API-level source of
truth and feeds the autodoc pages, so write it carefully (default + env var).

## Building the docs

From the repo root with the bundled venv (set cwd first):

```powershell
Set-Location d:\workspace\biomero-clone
.\venvTest\Scripts\python.exe -m sphinx -b html docs docs\_build
```

Output lands in `docs/_build`. Check the build for warnings about missing
references or malformed RST in the files you touched.
