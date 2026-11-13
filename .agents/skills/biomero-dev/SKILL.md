---
name: biomero-dev
description: BIOMERO core library development runbook. Use when changing biomero/slurm_client.py or related modules, reviewing component ownership and dependency reuse, debugging event-sourced workflow state and projections, adding or migrating a config option, writing or running pytest tests, following TDD, or building the Sphinx docs. Covers event and aggregate inspection, config precedence, the existing pytest fixture style, the venvTest test runner, and the docs build. Also use when drafting component release notes.
---

# BIOMERO Core Development

Read the [shared NL-BIOMERO engineering rules](https://github.com/NL-BioImaging/NL-BIOMERO/blob/master/.agents/skills/nl-biomero-engineering/SKILL.md)
first: they cover branches, pull requests, merging, agent guidance and local
checkouts. This skill adds only the BIOMERO core rules.

Use this skill for development work on the BIOMERO Python library in this repo
(`biomero/`), especially `biomero/slurm_client.py`. Favor matching existing
conventions over inventing new ones: read the surrounding code, mirror the
nearest existing example, then make the smallest change that is correct and
backward compatible.

Preserve supported deployment modes, configuration contracts and safe APIs.
Compatibility does not require preserving a bug: apply defect corrections by
default when safe, and do not add an opt-in gate merely to retain faulty behavior.
Distinguish bug fixes from optional new capabilities; retain justified explicit
opt-outs and older-client compatibility paths.

General documentation describes normal behavior and configuration. Put deployment
transitions and upgrade steps in dedicated upgrade guides or release notes, not
in ordinary feature/API explanations. Keep development history and the narrative
of how a bug was fixed in PRs or developer records.

## Design: reuse and responsibility

Prefer the simplest design that meets today's requirements. Judge complexity by
the responsibilities, interactions and lifecycle state a maintainer must
understand, not just line or class count; an abstraction should reduce that
burden for an actual need. This draws on Robert C. Martin's
[separation of policy and mechanisms](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
and Martin Fowler's [YAGNI](https://martinfowler.com/bliki/Yagni.html), without
requiring a new architectural framework or speculative flexibility.

BIOMERO provides bioimage/HPC workflow functionality and durable workflow state.
Reuse existing component functions and supported dependency APIs (DRY), keeping
generic execution mechanisms with the libraries that own them. For example,
BIOMERO decides whether an operation is safe to retry and how to reconcile a
Slurm job; Fabric/Paramiko own SSH execution and protocol mechanics.

Before adding an infrastructure wrapper, inspect the installed and supported
upstream APIs and the existing owner elsewhere in the stack. Isolate a missing
dependency behavior with a minimal reproduction and check existing open/closed
upstream issues before proposing a new report. Prefer native configuration or
a narrow supported extension; assess an upstream fix or small maintained patch
when the gap belongs there. Neither upgrading nor forking is an automatic fix.
Explain the remaining gap and maintenance cost before expanding our own layer.

Prefer the current functions, contracts and execution model. A new class or
module is a design change: justify it in the PR by a cohesive responsibility or
actual reuse, and why an existing module or another component cannot own it,
not merely by moving complexity out of a long function. A generic I/O proxy, watchdog or second
execution supervisor is a signal to reconsider ownership and simpler options.
New abstractions are allowed; demonstrate why they fit our responsibilities and
why existing libraries/components cannot supply the behavior. Passing tests
alone do not establish that the architecture is appropriate.

Prefer a small decorator for reusable function-level checks or retry behavior
when it keeps the operation readable and its policy visible at the definition.
Follow existing patterns such as `database.retry_on_database_conflict`; use
`functools.wraps` and preserve the calling contract. Decorate an explicitly safe
operation, not arbitrary `run()` calls or an entire side-effecting workflow.
Use a context manager for a bounded resource/configuration scope. These are
defaults, not a requirement to refactor unrelated code or hide a new execution
framework behind `@` syntax.

## Recovery means workflow continuity

The goal is to let users continue work after a transient failure without
orphaning costly HPC allocations. Loss of SSH observation is not evidence that
the Slurm job failed. Preserve workflow/task/job identity and durable progress,
reconnect and reconcile the existing allocation, then continue monitoring,
retrieval and finalization without duplicate jobs or imports.

Keep retries at explicitly safe domain-operation boundaries; an arbitrary
`run()` is not automatically replayable. Reuse the existing recovery budget,
submission reconciliation and transfer-publication safeguards. A wait alone is
not a workflow failure: retrying during the same outage may offer no benefit.
Where a stalled call prevents required recovery, heartbeat or cancellation,
prefer native bounds that return control; do not build a general SSH execution
framework to cover hypothetical stalls. Distinguish detection time, the outage
budget and legitimate healthy command duration.

Preserve intentional cancellation and the agreed terminal-attempt behavior when
the recovery window expires, retaining job IDs and recovery evidence. A restart
or outer supervisor must not silently reset that policy or resurrect terminal
attempts. Callbacks can expose heartbeat/cancellation to a caller, while the
OMERO implementation stays in its adapter layer.

Validation should show that the original Slurm allocation survives SSH loss and
supplies the completed results after recovery, with one submission/import.
Test silent stalls separately as a way to regain control, not as a replacement
for workflow-continuity acceptance.

## Component ownership and execution modes

- **Core (`biomero`)** owns reusable HPC/workflow operations, durable state,
  job identity/reconciliation and the detached launcher/claim/resume contracts.
  It provides mechanisms; callers (scripts, OMERO.biomero, the importer and the
  processor) decide whether to run a stage, which result path to take and how
  to react to its outcome.
- **`biomero-scripts`** owns the OMERO script adapter and workflow pipeline:
  inputs, session/group handling, script invocation and OMERO output/annotations.
  It calls core functionality rather than duplicating it.
- **`OMERO.biomero`** owns the web/admin interface, configuration presentation
  and launch/status interactions. It consumes shared contracts rather than
  becoming another workflow executor or recovery implementation.
- **NL-BIOMERO's `biomeroworker/processor.py`** supplies the optional detached
  execution host: worker lifecycle, concurrency, OMERO sessions and discovery of
  recoverable work after restart. It reuses core's persisted contracts and the
  scripts' workflow pipeline. It is an alternative to executing that pipeline
  inline, not another core library or a replacement for all scripts.

OMERO.biomero owns `[UI]` settings and workflow launch defaults, including Zarr
version, GPU preferences and optional comparison shortcuts. Resolve those biases
in its launch adapter and pass normal typed OMERO script parameters, with shared
keys in `biomero.constants`. Scripts and core must not read `[UI]` settings.
Scripts preserve parameter values through inline, detached and batched execution;
define missing-value defaults in the script contract rather than inferring UI state.

Keep component READMEs consistent with their existing level of detail. Link to
the canonical administrator guide for UI configuration instead of duplicating it
in core or scripts documentation. Document script parameters with their existing
parameter descriptions; put implementation rationale in the PR.

Process supervision and per-operation recovery solve different failures. Reuse
core recovery within the pipeline; do not stack an independent retry budget,
submission policy or result-finalization implementation in the processor.
Factor shared domain behavior into core and shared OMERO behavior into the
adapter/pipeline, rather than copying either between execution modes.

Detached execution is optional but strongly recommended for long workflows.
Maintain supported inline behavior and verify both modes when changing shared
functionality. Making detached execution mandatory in 3.0 is a possible future
direction, not a current requirement or permission to remove inline support.

### Core / OMERO scripts boundary

BIOMERO core owns event-store aggregates, historical replay, and versioned
metadata views or update plans expressed as plain data structures. It does not
own OMERO connections or annotation persistence.

- Never accept or use an OMERO connection, gateway, session, or model wrapper in
  core APIs. Optional imports and duck-typed connection adapters do not bypass
  this boundary.
- Do not introduce dependencies, imports, copied implementation code, or
  client-library-specific references to OME/OMERO libraries (omeropy, `omero-*`
  plugins, ezomero, ome-zarr) in core code or its required dependencies; core
  is Apache-2.0 and must stay licensable independently of them. Describe
  compatible data structures by their contract, not by a client library. The
  optional `full` extra is only an install list for biomero-scripts, which is
  installed by cloning and has no packaging of its own; it may list these
  libraries for the NL-BIOMERO containers, but nothing in `biomero/` may rely on
  it.
- Keep OMERO reads/writes, annotation creation/update/unlinking, permissions,
  session keepalives and persistence backups in biomero-scripts. Scripts request
  a view from core and apply it to OMERO; they must not pass the connection back
  into core to perform that work.
- Test rendering and event-store view logic in core; test OMERO adapters in the
  scripts test harness. Preserve the repositories' separate dependency and
  licensing boundaries.

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
- [references/eventsourcing.md](references/eventsourcing.md): inspect immutable
  notifications, replay workflow or task aggregates at exact versions, compare
  aggregates with SQL projections and exported metadata, and rebuild derived
  views. Read this before diagnosing workflow lifecycle, status ordering,
  projection lag, or metadata provenance.

## Core Rules

- Keep release pins in dependency declarations and `slurm-config.ini`, including
  workflow, converter and shallower image/tool versions. Do not hardcode those
  releases in Python defaults or repeat them in documentation examples, tables
  or prose. Document the setting and refer to the maintained ini for the chosen
  release, so component releases do not require code or documentation edits.
  This concerns software release pins, not meaningful contract/view identifiers
  such as the metadata view `v0` or explicitly supported data-format versions.
  A generic `latest` image fallback is allowed; recommend a deployment ini pin
  and record the actual installed tool version for provenance and recovery.
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
- `slurm_client.py` is the reference style for every module in `biomero/`:
  Google-style docstrings (summary line, then `Args:` with `name (type):`,
  `Returns:` and `Raises:`) on public and non-trivial private functions, type
  hints on all signatures, descriptive names (`slurm_client`, not `client`)
  and double-quoted strings. New and touched modules follow it.
- Keep container acquisition portable across HPC environments. Use the
  administrator-selected Apptainer or Singularity executable for registry
  resolution, pulling, and SIF inspection; do not introduce a separate registry
  client as a required dependency when the native runtime provides the needed
  behavior.
- Keep scheduled image-pull resource precedence portable: a non-empty
  ``image_pull_*`` value overrides the matching global ``sbatch_*`` value;
  otherwise inherit the global value, then let Slurm apply its scheduler
  default. Do not invent large CPU or memory requests. If no CPU allocation is
  exposed to the image worker, limit SquashFS to one process.
- Any change to an ini setting (new option, migration, or a change to its
  scope/precedence/meaning) is not done until every documentation and config
  surface is updated to match: the sample `resources/slurm-config.ini`, both
  Sphinx docs (`slurm-configuration.rst`, `configuration-reference.rst`), the
  deployment inis (e.g. `NL-BIOMERO/web/slurm-config.ini`), and the
  OMERO.biomero admin UI (`SettingsForm.js`) when the setting is editable there.
  See [references/docs.md](references/docs.md) for the full cross-surface
  checklist.

## Release notes

Before drafting a stable or beta release body, read the current
[shared component release-note guide](https://github.com/NL-BioImaging/NL-BIOMERO/blob/master/.agents/skills/omero-biomero-expert/references/component-release-notes.md).
Keep the format in that single source; do not copy it into this repository.

Each core release records its event history after publishing: run
`python tests/replay/record_history.py <version>` and commit the fixture
(skip it when no event class, aggregate or pinned persistence library
changed). Histories are never edited; the replay tests keep them working
(see `docs/developer/eventsourcing.rst`, "Event history replay").
