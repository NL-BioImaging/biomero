# OMERO workflow scripts

BIOMERO.scripts integrates BIOMERO with OMERO. Use the scripts directly from
OMERO or through [OMERO.biomero](https://github.com/NL-BioImaging/OMERO.biomero).
The source is maintained in the separate
[biomero-scripts repository](https://github.com/NL-BioImaging/biomero-scripts).

## Installation and upgrades

The [NL-BIOMERO deployment guide](https://nl-bioimaging.github.io/NL-BIOMERO/)
provides containers with compatible scripts, core and importer dependencies.
For custom deployments:

1. Install the corresponding `biomero[full]` release in the processor's Python
   environment, together with OMERO's supported script runtime and the external
   export/conversion tools required by your workflows.
2. Clone a released scripts tag into the server's `lib/scripts/biomero` directory.
   Select the tag from the coordinated deployment's component references.
3. Install the same scripts revision on the detached worker, whose supervisor
   loads the workflow pipeline from disk.
4. Confirm registration with `omero script list`.
5. Run **Slurm Init**, then **Slurm Check Setup**, to initialize cluster
   directories, scripts and images. Wait for the required images to be ready.

Upgrade the installed core, scripts and worker together. The scripts require
core's `biomero.provenance` and `biomero.maintenance` APIs even when shallow
storage or detached execution is disabled. The displayed script `VERSION`
identifies the release series; compare package versions and scripts tags when
checking prereleases. The deployment configuration and package dependency
declarations specify the maintained component versions.

## Script reference

### Main Workflow Scripts (`__workflows/`)
- **`SLURM_Run_Workflow.py`**: Primary workflow orchestrator with ZARR support
- **`SLURM_Run_Workflow_Batched.py`**: Batch processing variant for multiple datasets
- **`SLURM_CellPose_Segmentation.py`**: ⚠️ **EXAMPLE ONLY** - Manual single-workflow script for CellPose. Not installed by default in NL-BIOMERO. Use `SLURM_Run_Workflow.py` instead.

### Data Management Scripts (`_data/`)
- **`_SLURM_Image_Transfer.py`**: Export data from OMERO to SLURM (with cleanup)
- **`_SLURM_File_Transfer.py`**: Transfer a single OMERO FileAnnotation to a SLURM job's input directory (e.g. model weights, CSV config). Returns the resolved SLURM path for injection as a workflow CLI argument.
- **`SLURM_Remote_Conversion.py`**: Intelligent format conversion on SLURM
- **`SLURM_Get_Results.py`**: Upload workflow results back to OMERO (standard mode)
- **`SLURM_Import_Results.py`**: Import workflow results with full [BIOMERO.importer](https://github.com/NL-BioImaging/BIOMERO.importer) integration — selected automatically when `IMPORTER_ENABLED=true`
- **`SLURM_Get_Update.py`**: Monitor and update workflow status

### Administrative Scripts (`admin/`)
- **`SLURM_Init_environment.py`**: Initialize SLURM environment
- **`SLURM_check_setup.py`**: Validate BIOMERO configuration
- **`SLURM_Cownary.py`**: Run a fixed, admin-only lolcow cownary to verify SSH, Slurm scheduling, configured shared storage, and Singularity execution end to end. It accepts no command or path input and inherits BIOMERO's default partition, global `sbatch_*` settings, and configured Apptainer cache, temporary, and bind paths without allowing them to override the fixed cownary job scope.
- **`Tail_logs.py`**: View recent BIOMERO log entries (admin only)
- **`Example_Minimal_Slurm_Script.py`**: Administrator-only example for ad-hoc SSH diagnostics on the Slurm cluster. NL-BIOMERO does not install it by default.

### Workflow Process
1. **Export**: Selected data transferred from OMERO to SLURM cluster
2. **Convert**: Smart format conversion (with ZARR no-op optimization)
3. **Process**: Computational workflows executed on SLURM
4. **Monitor**: Job progress tracking and status updates (with real-time polling when SlurmClient is available)
5. **Import**: Results imported back to OMERO — via `SLURM_Import_Results.py` (importer-enabled) or `SLURM_Get_Results.py` (standard), selected automatically based on `IMPORTER_ENABLED`
6. **Cleanup**: Temporary artifacts automatically removed (non-critical cleanup errors are logged but do not fail the workflow)

### Optional detached execution

> **New in BIOMERO.scripts 2.9:** `BIOMERO_DETACHED_WORKFLOWS` is an opt-in
> feature flag. Installing the updated scripts does not change existing
> workflow behavior while
> `BIOMERO_DETACHED_WORKFLOWS` is absent or false. Existing and custom
> deployments remain inline until an administrator enables the feature and
> provides the required background worker supervisor.

Set `BIOMERO_DETACHED_WORKFLOWS=true` only when the deployment also provides a
compatible detached workflow supervisor, such as the `biomeroworker` in
NL-BIOMERO. `SLURM_Run_Workflow.py` and its batched variant then validate and
queue the request before returning. The supervisor performs transfer,
conversion, Slurm monitoring, and result import in the background.

Once the script reports that the workflow is queued in the background, the run
no longer depends on the browser tab or the OMERO session that submitted it.
Administrators do not need seven-day or infinite OMERO sessions, an unusually
large OMERO.web cookie age, or an open browser merely to cover the total Slurm
runtime. Ordinary timeouts must still cover the initial queue hand-off and each
OMERO-side transfer or import subprocess. If detached mode is absent, disabled,
or unsupported by the installed BIOMERO library, the scripts retain their
established inline behavior and the session must remain active.

See the [NL-BIOMERO detached-workflow administrator guide](https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/detached-workflows.html)
for deployment, recovery, and verification details.

### Dynamic Import Script Selection

The import step automatically selects the right script based on your environment:

| `IMPORTER_ENABLED` | Script used | Dataset import method |
|-|-|-|
| `false` (default) | `SLURM_Get_Results.py` | Upload via OMERO API |
| `true` | `SLURM_Import_Results.py` | In-place import from remote storage via BIOMERO.importer |

Set `IMPORTER_ENABLED=true` in your environment (e.g. docker-compose `.env`) to enable in-place imports via BIOMERO.importer. The script will raise an error at startup if `IMPORTER_ENABLED=true` but the `BIOMERO.importer` module is not installed.

## Inputs and results

### Shared group folder mappings

`SLURM_Import_Results.py` supports both the legacy
`/opt/omero/server/biomero-config.json["group_mappings"]` configuration and an
optional dedicated `/opt/omero/server/group-mappings.json` file. Mappings are
merged by group key. Entries found only in either source are retained, and the
dedicated file wins when the same group is present in both.

Override the default paths with `OMERO_BIOMERO_CONFIG_FILE` and
`OMERO_BIOMERO_GROUP_MAPPINGS_FILE`. Deployments that do not mount the dedicated
file continue using the legacy configuration unchanged.

When `BIOMERO_SHALLOW_ZARR=true`, Image Transfer also derives each group's
managed storage root at runtime as `IMPORT_MOUNT_PATH / mapping.folder` for
canonical Zarr promotion and reuse. There is no separate `storage_roots`
configuration. The processor worker must receive the same read-only mapping
files that OMERO.biomero edits and the same shared-storage mount; mappings are
read for each script execution, so runtime changes do not require an image
rebuild.

### Optional shallow Zarr storage

`BIOMERO_SHALLOW_ZARR` defaults to `false`. It is effective only together with
`IMPORTER_ENABLED=true`:

- false: Image Transfer exports normally and Import Results imports normally;
- true: Image Transfer may promote/reuse a verified canonical Zarr, and Import
  Results submits a typed `biomero.shallow-zarr` operation with the exact
  workflow input snapshot. By default, eligible results are normalized on
  Slurm before transfer. BIOMERO.importer validates the remote receipt and
  registers the results. When remote shallowing is disabled or safely falls
  back, the importer performs identity comparison and normalization locally.

Run Workflow distinguishes complete Zarr inputs from temporary conversion
material. A workflow that consumes Zarr receives a reconstructed shallow input
containing the canonical original pixels and every managed label. When the
selected workflow consumes TIFF, a shallow-backed OMERO Image instead follows
the established OMERO CLI Zarr export path: the Image's registered PixelBuffer
is exported as a standalone temporary Zarr and then converted to TIFF. This
preserves a selected mask Image as mask pixels, avoids transferring unrelated
original pixels and labels, and deliberately excludes the temporary export
from canonical promotion and returned-Zarr matching. Plates always use the
complete Zarr path.

The OMERO script delegates returned-Zarr hashing and normalization to the remote
helper or importer. If the deployed
importer does not advertise the lifecycle operation, or no canonical workflow
snapshot is available, it uses the established full-import path. Existing
legacy label-result controls remain unchanged in that fallback. Identity
worker concurrency for local normalization is configured on BIOMERO.importer;
remote concurrency uses `BIOMERO_REMOTE_SHALLOWER_WORKERS`. Once the importer
accepts an order, its processing is independent of the submitting script.

Canonical Plate identities are indexed in OMERO as one compact Plate record
plus bounded image- and label-node records. This keeps large Plate metadata
below OMERO/PostgreSQL MapAnnotation value limits; existing monolithic records
remain readable.

When Image Transfer reuses an existing managed backing Zarr (including imported
`.processed` stores), its pixels are authoritative for both Images and Plates.
The canonical record therefore has `canonicalPixelVerified=true` without an
additional pixel read through OMERO. Pixel identities are still calculated for
matching workflow results. Previously unverified records are upgraded on reuse
when the recorded import path identifies that same backing store; this creates
a new metadata generation without copying or rehashing its pixels.

Freshly exported canonical Zarrs follow a different path: their pixel identities
must match the source OMERO Images before promotion. Plate exports are checked
field-by-field using the exporter's well/field mapping, with connection keepalive
throughout verification. A mismatch prevents canonical promotion. Merely placing
an unrelated Zarr under a managed storage root does not make it authoritative.

Eligible Image results expose their labels as ordinary OMERO Image projections
until label-aware viewers are generally available. Eligible HCS results remain
one derived OMERO Plate: its WellSample pixels are served from the canonical
source Plate while the in-place shallow collection retains the image-level
labels. This avoids flattening a large Plate into thousands of loose mask
Images.

**Import Plate label preview** is an optional result setting, disabled by
default. It creates one additional Plate whose WellSample pixels point directly
at one common image-level label. Supply **Plate label preview name**, or leave it
empty only when exactly one label name occurs on every Plate image. The preview
creates OMERO objects and PixelBuffer links but does not copy label arrays.

Importer-disabled deployments continue to use `SLURM_Get_Results.py` and do not
load BIOMERO.importer Zarr helpers. The worker processor must forward this
environment variable to downloaded scripts; current NL-BIOMERO deployments do
that dynamically through `biomero.constants.slurm_env`.

### Optional remote Zarr shallower

Remote shallowing uses BIOMERO's shared Slurm job monitor with a script-owned
heartbeat callback that keeps the OMERO connection alive during shallowing
and recovery. Conversion uses the same callback interface.
Connection failures stop monitoring.
This applies to inline and detached workflows. Helper resources
inherit generic Slurm settings, with optional partition, memory and time
overrides in `[SLURM]` (`remote_shallower_partition`,
`remote_shallower_mem`, `remote_shallower_time`). GPU and job-array settings
are not inherited. Use matching BIOMERO core and scripts versions.

With administrator `BIOMERO_REMOTE_SHALLOW_ZARR=true`, importer enablement and
the existing shallow capability, `SLURM_Import_Results.py` runs the configured
CPU remote shallower before ZIP creation. It uses the canonical input manifest
already persisted by image transfer. Detached retries adopt the helper job or
completed receipt. Successful receipts come from workflow tracking and travel
in the ordinary lifecycle import order; the importer validates them without
repeating pixel hashing. Unsupported results and safe failures retain the local
importer path. Remote shallowing defaults to true within opt-in shallow Zarr
mode and is not an OMERO script parameter. Run `SLURM_Init_environment` to
install the image and verify it with `SLURM_check_setup` before running workflows.
Runtime never pulls images; a missing or invalid image raises a setup error.
Unresolved submissions, incomplete recovery or invalid receipts stop retrieval
and preserve remote output for inspection instead of archiving uncertain data.
Set `BIOMERO_REMOTE_SHALLOW_ZARR=false` to retain importer-side normalization.
Shallow storage itself remains opt-in: an absent or false `BIOMERO_SHALLOW_ZARR`
leaves ordinary result imports unchanged.

See the [NL-BIOMERO remote-shallower administrator guide](https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/remote-shallower.html)
for deployment settings, helper image initialization and recovery.

### Workflow metadata

New result annotations use the legacy-compatible `v0` view. Scientific task
parameters and job fields are retained; internal detached coordination tasks and
unused workflow parameters are excluded. Recorded shallow/full storage facts,
container identity and canonical biocodes are included when available.
Full CSV provenance and event history are preserved.

For existing results, administrators can preview or apply a refresh through
Slurm Init. See the
[metadata administration guide](https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/metadata-refresh.html).
Rendering policies and adapter APIs are documented in
[Workflow metadata views](developer/metadata-views.rst).

### Workflow provenance files and searchable metadata

Both result scripts always attach `metadata_<workflow UUID>.csv` (or the job
ID when no workflow UUID is available), independently of ZIP and individual
file-output options. Importer results attach it to the discovered result Plates
or destination Dataset; classic pixel uploads attach it to the result Dataset.
For attachment-only workflows, the existing result/log targets are used.
Explicitly selected legacy attachment targets continue to receive the CSV.

The importer route uses the existing in-place upload helper when enabled and
available, with regular upload otherwise. The classic route uploads the file
before cleaning temporary storage. The full `metadata.csv` beside importer
results remains unchanged for re-importing an analyzed directory, including its
existing `csv_` key prefix in importer annotations. This change does not alter
the importer's independent metadata reader or its error handling.

MapAnnotations remain a searchable view of the full CSV and workflow history.
The scripts first try all existing fields and values. Only after an index-size
rejection do they retry with large fields represented by the CSV filename,
UTF-8 value size and SHA-256 checksum. Smaller fields remain searchable;
accepted large values are unchanged. One rejected annotation does not prevent
later task/job annotations. Reports distinguish complete, reduced and incomplete
views, and CSV link failures are counted per target. No database changes or
feature flag are required. Files are snapshots of the workflow state available
at export time, rather than the eventual final lifecycle state.

### Optional ROI postprocessing

`SLURM_Run_Workflow.py` can optionally turn imported grayscale label images
into ROIs on their exact source images. Enable **Create ROIs from label
images**, import the image results into a Dataset or Screen, and choose Polygon
or Mask output. BIOMERO records each imported label-image ID together with the
source-image ID it matched and passes those explicit pairs to the OMERO
`Labels2Rois` utility script after import.

Created ROI names use `workflow_name__workflow_uuid__label_value`, making them
filterable by algorithm or by an exact workflow run. By default, the workflow
UUID also selects a deterministic color from a curated palette, so separate
ROI runs are visually distinct. An optional `#RRGGBB` override can be supplied
by clients such as OMERO.biomero. Labels2Rois applies that color as a
translucent Mask fill or as a Polygon fill and outline. The optional **Clear
existing ROIs on original images** setting forwards the native clear behavior;
its case-sensitive name filter limits deletion, while an empty filter clears
all existing ROIs on each original image. Clearing is disabled by default.

Imported label images are retained in OMERO by default. The optional **Delete
from OMERO after ROI creation** setting forwards the native `Labels2Rois`
cleanup flag, which deletes each imported label image only after its ROI
conversion succeeds. This removes only the OMERO image; workflow result files
in remote storage (including importer `.analyzed` storage) are preserved.

If every image output in the selected workflow descriptor has subtype `label`,
all imported images are selected automatically. For mixed or descriptor-less
workflows, BIOMERO groups imported results by their matched source image. A sole
result is selected directly; with multiple results, label-like names such as
`mask`, `label`, or `segment` are selected. Ambiguous groups are skipped without
failing import. The lower-level result scripts retain an optional glob such as
`*_cp_masks.tif` as an advanced override, matched before result-image renaming.
A missing `Labels2Rois` script disables this optional step with a warning.
Import and workflow completion remain successful, and result images are
retained, if selection is ambiguous, the utility is missing, or postprocessing
fails.

## Logging and monitoring

The script's `Message` output is the concise summary shown in OMERO Activities.
Detailed INFO-level execution logs are available behind the activity's info
button. DEBUG logs are written to the worker's
`/opt/omero/server/OMERO.server/var/log/biomero.log`, with rotation at
500 MB and nine backups. Detached maintenance continues logging in the worker;
its request ID connects the initial activity to subsequent progress.

Use **Slurm Check Setup** for configuration, image acquisition and metadata
maintenance status. **Slurm Get Update** monitors analysis jobs and retrieves
`omero-<job-id>.log`. Image acquisition has separate per-task logs under
`<slurm_script_path>/image-pulls`.

## Running a workflow

1. Select the input Images, Dataset or Plate in OMERO.
2. Run **Slurm Run Workflow** and select the configured workflow and version.
3. Set workflow parameters and choose the required result destination and options.
4. Enable **Use ZARR Format** for a workflow configured to consume Zarr directly.
5. Submit the run and follow its status. In detached mode, the browser can be
   closed after the script confirms the background handoff.

For manual operation, Image Transfer exports inputs, Remote Conversion changes
formats when required, and the appropriate result script retrieves outputs.
`SLURM_CellPose_Segmentation.py` is a manual example, not the general runner.
