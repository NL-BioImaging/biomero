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

``v0`` is the only supported view, using the legacy-compatible layout. It retains
scientific task parameters, including false and zero values, and the existing
task/job fields.
It excludes detached launcher and result-shallower coordination annotations,
duplicate ``output_settings`` parameters, and unselected workflow parameters
in orchestration tasks where workflow selection is known. These records remain
available in the event store and full CSV.

Job ``Command``, ``Env_*`` and ``Result_Message`` fields are retained, as are the
``Name``, ``Input_Data`` and ``Created_On`` fields used by batching.
Import-result tasks retain the ``SLURM_Get_Results.py`` namespace used for result
discovery. Canonical and shallow-Zarr metadata use separate namespaces and are
not changed by this policy.

The view adds ``Metadata_View_Version`` and ``Aggregate_Version``. The
existing ``Version`` field still describes software, not the metadata schema.
A workflow and its tasks have independent aggregate versions. Metadata written
during import can therefore legitimately retain ``IMPORTING`` even after the
workflow finishes. Refreshing its view does not advance that snapshot.

Result storage provenance
-------------------------

The view includes additive storage fields on the import task when the event
store contains observed provenance for the requested ``target_key`` (for example
``Plate:123``). ``Storage_Format`` distinguishes ``shallow-zarr`` from
``full-zarr`` and ``Storage_Shallow`` is an explicit boolean string. These are
per-result facts, not workflow feature flags.

The scripts collect importer outcome receipts and the result's shallow manifest,
then record a ``Task.StorageProvenanceRecorded`` event before rendering metadata.
Core stores plain evidence and renders it without accessing files or connections.
Recorded execution location, tool version, container reference, remote task/job
IDs and report checksum are retained. Missing historical tool or
location evidence is not inferred from current configuration.

Canonical source biocodes appear inline when small. Larger collections use a
count and reference to ``Shallow_Manifest`` with its SHA-256 checksum. Full
per-target evidence also remains in CSV provenance and the event store.
``render_workflow_metadata`` and ``plan_metadata_refresh`` accept an optional
``target_key``; callers must supply it to select a result's storage facts.

Existing snapshots without this event remain unchanged by a historical refresh;
the updater does not invent missing storage history from current disk contents.

Planning a view refresh
-----------------------

Core creates data structures; the scripts layer owns connections, backups and
annotation updates. Core does not import annotation client libraries.

.. code-block:: python

   from biomero.provenance import MetadataAnnotation, plan_metadata_refresh

   existing = [
       MetadataAnnotation(namespace=namespace, values=values)
       for namespace, values in stored_annotations
   ]
   changes = plan_metadata_refresh(
       tracker, workflow_uuid, existing, view_version="v0")

Each change contains a before and after view. An absent after view requests
removal of the object's link to that annotation, not deletion of the annotation.
The caller is responsible for applying these changes safely.

Legacy snapshots are resolved by an exact, unique ``Modified_On`` match against
aggregate history. New annotations use their explicit aggregate version.
Ambiguous or incomplete snapshots and conflicting identities are refused rather
than guessed. Unknown namespaces and additional custom keys are preserved.
Existing CSV references for oversized values are retained.

The administrative refresh adapter is provided by biomero-scripts in
``admin/SLURM_Init_environment.py`` as an optional metadata refresh operation.
Its documentation describes dry runs, backups,
shared-annotation checks and updating existing views in place. No automatic
migration runs during initialization. New result scripts continue to write
``v0``.

Versioning and compatibility
----------------------------

``Metadata_View_Version`` identifies the rendering policy; ``Aggregate_Version``
identifies the exact event-sourced snapshot. Neither changes the existing
software ``Version`` field. A refresh reapplies the selected policy to the
original snapshots, not the latest workflow state. It does not rewrite events,
rerun analysis or upgrade the contents of existing CSV attachments.

``v0`` restores the pre-detached task/job layout while adding revision markers
and recorded storage provenance. It is not a byte-for-byte reproduction:
internal launcher/helper annotations and excluded parameters are removed, and
fields emitted by the renderer may be added to an existing annotation. Unknown
custom fields and existing reduced CSV references are preserved. Missing
annotations or unresolved historical snapshots cause the planner to refuse
the update rather than synthesize a partial history.

Callers should use a dry run to inspect the proposed field changes before
applying a refresh. Core returns ``MetadataChange`` objects; the scripts decide
how to display differences, persist backups and update annotation links.

Detached administrative refresh
-------------------------------

When detached execution is enabled, compatible scripts can queue an apply
request through ``biomero.maintenance.queue_metadata_refresh``. The returned
UUID identifies the maintenance request, not a workflow being refreshed. One
request may cover many workflow/result pairs. Dry runs remain inline.

``MetadataRefresh`` records ``QUEUED``, ``RUNNING`` and ``DONE``/``FAILED`` states
with compact progress counters. ``metadata_refresh_statuses(tracker)`` returns
active and recent terminal requests as plain data; the Check Setup script
exposes this information to administrators. These requests do not appear as
analysis workflows in the progress projection.

After a worker interruption, the supervisor can retry an unfinished sweep.
This relies on idempotent annotation updates in the scripts, not a core
checkpoint for every result. Completed and failed requests are not retried
automatically. For execution policy, see the
`supervisor documentation
<https://nl-bioimaging.github.io/NL-BIOMERO/master/developer/detached-workflow-supervisor.html#metadata-maintenance>`_.
