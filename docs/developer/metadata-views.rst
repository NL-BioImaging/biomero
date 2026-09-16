Workflow metadata views
=======================

OMERO MapAnnotations are a searchable view of workflow history, not the source
used for detached recovery. The event store remains authoritative. Full CSV
provenance is exported independently and is not reduced by these view policies.

The core function ``biomero.provenance.render_workflow_metadata(tracker,
workflow_id)`` renders namespace/value pairs without writing events or OMERO
objects. Both result scripts use this function. Deploy matching scripts and
core revisions together.

View policies
-------------

``v0`` is the default, legacy-compatible layout. It retains scientific task
parameters, including false and zero values, and the existing task/job fields.
It excludes detached launcher and result-normalizer coordination annotations,
duplicate ``output_settings`` parameters, and unselected workflow parameters
in orchestration tasks where workflow selection is known. These records remain
available in the event store and full CSV.

``v1`` is an explicit, slimmer view. It additionally omits job ``Command``,
``Env_*`` and ``Result_Message`` fields. Scientific parameters and the
``Name``, ``Input_Data`` and ``Created_On`` fields used by batching remain.
Import-result tasks retain the ``SLURM_Get_Results.py`` namespace used for result
discovery. Canonical and shallow-Zarr metadata use separate namespaces and are
not changed by either policy.

Both policies add ``Metadata_View_Version`` and ``Aggregate_Version``. The
existing ``Version`` field still describes software, not the metadata schema.
A workflow and its tasks have independent aggregate versions. Metadata written
during import can therefore legitimately retain ``IMPORTING`` even after the
workflow finishes. Refreshing its view does not advance that snapshot.

Refreshing existing annotations
-------------------------------

An administrator can explicitly refresh one result Image or Plate using the
Python API. No automatic migration runs during initialization, and there is
currently no deployment-wide setting for selecting ``v1`` on new writes.
New result scripts continue to write ``v0``.

.. code-block:: python

   from biomero.metadata_refresh import refresh_workflow_metadata

   # conn is an administrator's BlitzGateway; tracker is WorkflowTracker.
   plan = refresh_workflow_metadata(
       conn, tracker, "Plate", plate_id, workflow_uuid, view_version="v1")
   # Inspect the dry-run plan before applying. Use a private backup location.
   result = refresh_workflow_metadata(
       conn, tracker, "Plate", plate_id, workflow_uuid,
       view_version="v1", dry_run=False,
       backup_path="/private/backups/plate-metadata-before-refresh.json")

The updater edits retained MapAnnotations in place, preserving their IDs,
namespaces and creation events. Obsolete internal-task annotations are unlinked
from the selected object, not globally deleted. Reapplying a view is idempotent.
The backup contains original key/value pairs and annotation IDs, including
unlinked annotations; protect it like other provenance containing execution
details. Restore retained values with ``MapAnnotationWrapper.setValue`` and
``save``; unlinked original annotations can be linked to the object again.

Legacy snapshots are resolved by an exact, unique ``Modified_On`` match against
aggregate history. New annotations use their explicit aggregate version.
Ambiguous or incomplete snapshots, conflicting identities, shared annotations,
or duplicate non-list keys are refused rather than guessed. Repeated
``Input_Data`` keys retain the legacy list representation. Unknown namespaces
and additional custom keys are preserved. Existing CSV references for oversized
values are retained.

Run refreshes while metadata writers for the selected result are idle. The
updater checks for intervening changes, but multiple OMERO writes are not one
transaction. Errors propagate; inspect the backup and current annotations
before retrying a partially applied refresh. Image data, full CSV attachments,
shallow/canonical metadata and events are never rewritten by this API.
