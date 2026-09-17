BIOMERO components
==================

BIOMERO is a collection of cooperating services and libraries. For complete
deployment instructions, start with the
`NL-BIOMERO documentation <https://nl-bioimaging.github.io/NL-BIOMERO/>`_.

* **BIOMERO core** (this package) manages Slurm workflows, container acquisition,
  event-sourced tracking, and analysis views.
* `BIOMERO.scripts <https://github.com/NL-BioImaging/biomero-scripts>`_ integrates
  workflow execution, result retrieval, and metadata persistence with OMERO.
  See :doc:`scripts` for installation and the script reference.
* `BIOMERO.importer <https://github.com/NL-BioImaging/BIOMERO.importer>`_ prepares
  and registers imported data with OMERO.
* `OMERO.biomero <https://github.com/NL-BioImaging/OMERO.biomero>`_ provides the
  OMERO.web user and administrator interfaces.
* `BIOMERO Schema <https://nl-bioimaging.github.io/biomero-schema/>`_ defines
  shared workflow descriptors, canonical Zarr records, and normalization
  reports and receipts.
* `BIOMERO.shallower <https://github.com/NL-BioImaging/BIOMERO.shallower>`_
  provides filesystem-only shallow-Zarr result normalization. The importer
  uses the shared implementation locally; core can run its CPU-only container
  on Slurm before result archiving and transfer.

BIOMERO.shallower
-----------------

The helper compares returned image and label identities with the exact
canonical-input snapshot. Verified duplicate arrays are replaced by canonical
references, while new or changed data is retained. It does not connect to
OMERO or require OMERO credentials, and it does not suppress result
registration. Its initial adapter supports NGFF 0.4 / Zarr v2 Images and Plates.

Shallow storage is optional. When it is enabled, remote normalization is the
preferred path; administrators can choose local normalization instead. Trusted
receipts allow the importer to validate completed remote normalization without
hashing the omitted pixels again. Installing the library or helper alone does
not enable shallow storage.

For enablement, image acquisition, resource configuration, and recovery, see
the `remote shallower administration guide
<https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/remote-shallower.html>`_.
For file formats and service boundaries, see
`remote shallower contracts
<https://nl-bioimaging.github.io/biomero-schema/remote-shallower-contracts/>`_.
For the shared implementation, command reference, and container releases, see
the `BIOMERO.shallower documentation
<https://nl-bioimaging.github.io/BIOMERO.shallower/>`_.

For core's execution APIs and the distinction between worker restart, helper
recovery and user-requested reruns, see
:doc:`developer/execution-and-storage`. Searchable provenance and administrative
refresh requests are described in :doc:`developer/metadata-views`.
