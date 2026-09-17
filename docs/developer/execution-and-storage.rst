Execution, storage and recovery
===============================

BIOMERO core provides Slurm execution and durable workflow state. Workflow
scripts decide which pipeline stages to run; the NL-BIOMERO processor owns
background execution; the importer owns OMERO registration and local storage
preparation. These boundaries apply to inline and detached workflows alike.

Independent options
-------------------

``BIOMERO_DETACHED_WORKFLOWS`` selects background execution in compatible
scripts and workers. It is false when absent. Core centralizes the variable
names and persists the workflow and task aggregates; constructing a
``SlurmClient`` does not start a supervisor.

``BIOMERO_SHALLOW_ZARR`` enables canonical caching and shallow result storage
in the scripts/importer integration. It is also false when absent. Shallow
results reference verified canonical arrays instead of storing duplicate
pixels. New and changed image or label arrays remain in the result. Canonical
sources must remain available for those references to resolve. Disabling new
shallowing does not make existing shallow data independent of its sources.

``remote_shallow_zarr`` selects where eligible results are normalized. Its
default is true, so enabling shallow storage prefers the remote CPU helper.
Set it to false to normalize locally in the importer. It does not itself
enable shallow storage or detached execution. The scripts enforce importer
and shallow-storage eligibility; core additionally requires workflow tracking
and a nonempty canonical-input manifest before submitting the helper.

The scripts/importer establish canonical identity and pixel verification;
core transports and validates their manifest rather than reading OMERO pixels.
Reused registered Zarr and newly exported Zarr have different verification
paths in that integration. A core caller must provide the actual canonical
snapshot, not infer it from current feature flags.

Remote result lifecycle
-----------------------

After analysis and before archiving, the result script calls
``SlurmClient.shallow_results_on_slurm(data_path, workflow_id,
canonical_inputs, heartbeat=...)``. Core:

1. Locates or creates the ``_SLURM_Remote_Shallower`` task for that result path.
2. Checks the installed helper image and publishes the canonical manifest.
3. Submits or adopts a CPU Slurm job and monitors it through ``SlurmJob``.
4. Validates the report against the canonical inputs, task/job identity,
   container reference and tool version before completing the helper task.

The caller owns the heartbeat callback and any OMERO session it keeps alive.
Callback exceptions propagate to the caller. Core accepts no OMERO connection.
``get_remote_shallower_receipts()`` reads completed receipts from the event
store without contacting the cluster. The importer validates applicable
receipts before accepting remotely normalized results.

Image acquisition is an initialization operation. Workflow execution does not
pull a missing image. See :doc:`../configuration-reference` for resource
precedence, image configuration and setup checks.

Recovery boundaries
-------------------

**Worker restart.** The NL-BIOMERO supervisor discovers unfinished detached
requests and invokes the scripts' pipeline with ``resume=True``. This is a
Python argument supplied by the supervisor, not an environment variable.
The scripts use recorded stages and job IDs to skip completed work or adopt
existing Slurm jobs. Core supplies the persisted state and job monitoring;
it does not independently restart an entire pipeline. Slurm jobs may continue
while the worker is unavailable. Recovery requires the tracking database and
the corresponding input, output and container files to survive.

Launcher ``CLAIMED`` is coordination state, not an analysis phase or proof
that a workflow is still running. Workflow completion is recorded separately.
The supported supervisor topology is one active supervisor per tracking
database. A local remote-helper submission lock does not make the whole
pipeline safe for multiple supervisors.

**Remote helper interruption.** Core reuses a validated persisted report or
adopts the recorded Slurm job. Submission intent, job ID and a remote file lock
protect the helper submission; accounting is used to reconcile an interrupted
submission. An ambiguous intent raises an error instead of submitting again.
Unavailable job status also raises; it is not evidence that recovery is needed.

If the helper job ends unsuccessfully, core submits or adopts a separate
``--recover-only`` job using the recorded helper image. Its transaction
recovery must complete and the resulting report must validate before the
caller may archive results. A rejected initial submission can retain full
results for local import. Missing images, changed or missing submitted
manifests, invalid reports and unresolved recovery stop retrieval. These
errors do not guarantee automatic retry by the outer pipeline.

Keep the recorded SIF and remote state directory until completion. Existing
tasks retain their image and tool version even if deployment defaults change.
The helper's recovery guarantees do not imply exactly-once execution of every
analysis, transfer or import stage in the wider workflow.

**User-requested rerun.** Reusing recorded settings or restarting from the web
interface is an OMERO.biomero/scripts operation. It is distinct from adopting
an interrupted job and does not follow merely from core aggregate replay.

For supervisor discovery, batching and restart policy, see the
`NL-BIOMERO developer guide
<https://nl-bioimaging.github.io/NL-BIOMERO/master/developer/detached-workflow-supervisor.html>`_.
For the canonical and receipt formats, see the
`schema contracts
<https://nl-bioimaging.github.io/biomero-schema/remote-shallower-contracts/>`_.

State and metadata
------------------

Aggregate replay reconstructs recorded history. SQL progress/analytics tables
and OMERO key-value annotations are separate derived views. Rebuilding SQL
views does not update existing annotations or resume a failed workflow.
The progress projection prevents remote-helper coordination from replacing
the visible analysis task, while the underlying events remain available.

See :doc:`eventsourcing` for replay and projections, and
:doc:`metadata-views` for the ``v0`` renderer, historical refresh planning and
per-result shallow-storage provenance.
