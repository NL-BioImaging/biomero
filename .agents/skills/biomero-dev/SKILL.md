---
name: biomero-dev
description: BIOMERO core library (pylib) development runbook for the surf branch. Use when changing biomero/slurm_client.py or related modules, adding or migrating a config option, writing or running pytest tests, following TDD, or building the Sphinx docs. Covers the config precedence pattern (code default -> ini -> env), the existing pytest fixture style, the venvTest test runner, and the docs build.
---

# BIOMERO Core Development

Use this skill for development work on the BIOMERO Python library in this repo
(`biomero/`), especially `biomero/slurm_client.py`. Favor matching existing
conventions over inventing new ones: read the surrounding code, mirror the
nearest existing example, then make the smallest change that is correct and
backward compatible.

Backward compatibility is a hard requirement. Every new option must default to
the current behavior so existing deployments are byte-for-byte unaffected unless
they opt in.

## Environment

Tests and docs run from the repo-local `venvTest`. Run everything from this
repository's root (the folder containing this `.agents` directory). The terminal
cwd can reset between calls, so re-set it to the repo root first if needed
(PowerShell):

```powershell
# from the repo root
.\venvTest\Scripts\python.exe -m pytest tests/unit/test_slurm_client.py -q
```

## Reference Routing

Read only the relevant reference before acting:

- [references/testing.md](references/testing.md): pytest fixture style, the
  factory fixtures, TDD red/green discipline, the exact-kwargs `test_from_config`
  guard, and how to run a narrow vs full slice.
- [references/config-pattern.md](references/config-pattern.md): the
  `_get_config_value()` precedence helper and the full checklist of touch points
  for adding or migrating a config option.
- [references/docs.md](references/docs.md): every place a setting must be
  documented (sample ini, both Sphinx rst files, deployment inis, and the
  OMERO.biomero admin UI), the cross-surface consistency checklist, and how to
  build the Sphinx site.

## Core Rules

- New config follows the precedence pattern: code default -> `slurm-config.ini`
  -> environment variable, resolved by `_get_config_value()`. Never hand-roll
  `os.getenv` precedence in `from_config()`.
- A new constructor parameter on `SlurmClient` requires updating the exact-kwargs
  assertion in `test_from_config` (it asserts the full call to the constructor).
- TDD means writing tests that FAIL first, then implementing until they pass. If
  you batch the implementation with the tests, prove the tests are meaningful by
  temporarily disabling the new code and observing the red, then restore.
- Keep `pep8`/line-length style consistent with the file you edit; the module
  already carries many pre-existing long-line lints, so do not reflow unrelated
  code.
- Any change to an ini setting (new option, migration, or a change to its
  scope/precedence/meaning) is not done until every documentation and config
  surface is updated to match: the sample `resources/slurm-config.ini`, both
  Sphinx docs (`slurm-configuration.rst`, `configuration-reference.rst`), the
  deployment inis (e.g. `NL-BIOMERO/web/slurm-config.ini`), and the
  OMERO.biomero admin UI (`SettingsForm.js`) when the setting is editable there.
  See [references/docs.md](references/docs.md) for the full cross-surface
  checklist.
