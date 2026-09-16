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
       tracker, workflow_uuid, existing, view_version="v1")

Each change contains a before and after view. An absent after view requests
removal of the object's link to that annotation, not deletion of the annotation.
The caller is responsible for applying these changes safely.

Legacy snapshots are resolved by an exact, unique ``Modified_On`` match against
aggregate history. New annotations use their explicit aggregate version.
Ambiguous or incomplete snapshots and conflicting identities are refused rather
than guessed. Unknown namespaces and additional custom keys are preserved.
Existing CSV references for oversized values are retained.

The administrative refresh adapter is provided by biomero-scripts in
``admin/SLURM_Refresh_Metadata.py``. Its documentation describes dry runs, backups,
shared-annotation checks and updating existing views in place. No automatic
migration runs during initialization. New result scripts continue to write
``v0``; there is currently no deployment-wide setting to select ``v1`` for new
writes.
