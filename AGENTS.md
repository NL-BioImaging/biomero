# biomero

BIOMERO core: the Python library (`biomero/`, notably `slurm_client.py`) that
runs bioimage-analysis workflows on Slurm/HPC clusters with durable, event-sourced
workflow state. It does not own OMERO connections or the web UI.

## Read first

1. Shared rules for all component repositories:
   <https://github.com/NL-BioImaging/NL-BIOMERO/blob/master/.agents/skills/nl-biomero-engineering/SKILL.md>
2. Independent PR review procedure:
   <https://github.com/NL-BioImaging/NL-BIOMERO/blob/master/.agents/skills/nl-biomero-review/SKILL.md>
3. This repository's development rules (ownership and boundaries, config
   precedence, testing, documentation, release notes):
   [`.agents/skills/biomero-dev/SKILL.md`](.agents/skills/biomero-dev/SKILL.md)
   and the `references/` it routes to.

## Layout

- `biomero/`: the library (Slurm client, event sourcing, projections, constants).
- `resources/`: sample `slurm-config.ini`.
- `tests/unit/`, `tests/integration/`: pytest suites.
- `docs/`: Sphinx documentation (`docs/requirements.txt` lists its dependencies).
- `pyproject.toml`: dependencies and the `test` and `full` extras.

## Build and test

- Install: `python -m pip install --editable .[test]`
- Unit tests (CI): `python -m pytest tests/unit/ --cov=biomero --cov-report=xml`
- Narrow run: `python -m pytest tests/unit/test_slurm_client.py -q`
- Lint (CI, advisory): `flake8 biomero tests --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics`
- Docs: `python -m pip install -r docs/requirements.txt` then
  `python -m sphinx -b html docs docs/_build/html`
- CI: `.github/workflows/` runs the tests, the docs build and the package release.

## Related repositories

NL-BIOMERO (deployment stack), biomero-scripts, BIOMERO.importer, OMERO.biomero,
BIOMERO.shallower, biomero-schema, BIOMERO.ZarrViewer, all under
<https://github.com/NL-BioImaging>. Each has its own `AGENTS.md`.
