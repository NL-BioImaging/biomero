# Diagnosis of Slurm Job Failures on SURF Spider Cluster

## Executive Summary

The primary cause of the **conversion step failures** is a storage mount mismatch on the Spider cluster. The CephFS project storage `/project/biomero` is **not mounted** on compute node `wn-dc-15` (verified). This causes 100% of conversion jobs landing on that node to fail.

---

## Background: How BIOMERO Uses Spider

BIOMERO is a workflow orchestration system that runs image analysis pipelines on HPC clusters via Slurm. It is hosted on a VM at SURF (project `rsc_co_202570`) and submits jobs to Spider as Slurm user `biomero-sloev`.

**The conversion step** is a mandatory pre-processing stage that converts images stored in OMERO as OME-Zarr into TIFF format, as required by certain analysis workflows. It is submitted as a Slurm job array:

```bash
sbatch --job-name=conversion \
       --export=ALL,CONFIG_PATH="$PWD/$CONFIG_FILE" \
       --array=1-$N \
       /project/biomero/Share/biomero/slurm-scripts/convert_job_array.sh
```

Key facts about the conversion job:
- **Partition**: `normal` (Spider default — no explicit `--partition` is set for conversion jobs, by design, to avoid GPU partition costs)
- **Job script**: `/project/biomero/Share/biomero/slurm-scripts/convert_job_array.sh`
- **Input data path**: `/project/biomero/Share/biomero/data/<job-specific-subdir>/`
- **What the job does**: Each array task reads an OME-Zarr input path from a config file (`$CONFIG_PATH`), runs the `biomero-converter` Singularity/Apptainer container to convert it to TIFF (required by certain analysis workflows), and deletes the source `.zarr` on success
- **Required mount inside the job**: `/project/biomero` must be accessible on whichever node the array task is scheduled to

---

## Detailed Findings

### Unmounted `/project/biomero` on `wn-dc-15` (Conversion Failure)

* **Symptom**: Conversion jobs fail with exit code `1:0` and the error:
  ```text
  No corresponding input file for task X.
  ```
  This is the `convert_job_array.sh` error branch triggered when `[ -e "$file_to_convert" ]` is false — i.e. the input `.zarr` path does not exist on the executing node.

* **Statistical evidence** (`sacct` over ~2 hours of workshop traffic, 2026-06-29):
  - **23 conversion failures: all on `wn-dc-15`, zero exceptions**
  - All conversion jobs on `wn-la-07`, `wn-la-09`, `wn-la-18`, `wn-dc-09`: `COMPLETED 0:0`
  - Within the same job arrays, tasks scheduled on `wn-la-*` nodes completed successfully while tasks scheduled on `wn-dc-15` failed — confirming the failure is node-specific, not data-specific

* **Direct verification** — we submitted a targeted probe job pinned to `wn-dc-15`:
  ```bash
  sbatch --nodelist=wn-dc-15 \
         --job-name=test-mount \
         --output=/home/biomero-sloev/test-mount-%j.out \
         --wrap="echo === /project listing ===; ls /project/; \
                 echo === mount grep biomero ===; \
                 mount | grep biomero || echo NOT MOUNTED"
  ```
  Job `37069917` completed (`COMPLETED 0:0`) on `wn-dc-15`. Output:
  ```
  === /project listing ===
  accretion  afrijnmaas  ... hamres  harmony  ... tropl2  tropomi  ...
  [biomero is NOT listed]

  === mount grep biomero ===
  NOT MOUNTED
  ```
  Dozens of other projects (`hamres`, `tropl2`, `tropomi`, etc.) are correctly mounted on `wn-dc-15`. The `/project/biomero` CephFS volume is absent entirely.

---

## Suggested Solutions

### Permanent Solution (Recommended)

**Contact SURF Spider Support**: Ask the SURF administrators to mount the CephFS volume `/project/biomero` on **all `normal` partition compute nodes**. The volume is confirmed missing on `wn-dc-15`; other `wn-dc-*` nodes may be affected as well (unverified).

Relevant details for the support ticket:
- Project: `biomero` (CephFS at `/project/biomero`)
- Slurm user: `biomero-sloev`
- Affected node confirmed: `wn-dc-15` (normal partition)
- Working nodes: `wn-la-07`, `wn-la-09`, `wn-la-18`

### Temporary Workaround (BIOMERO Configuration)

If SURF cannot fix this immediately, exclude the problematic node(s) by modifying the conversion command in `biomero/slurm_client.py` (in `get_conversion_command`, around line 2385):

```python
conversion_cmd = "sbatch --job-name=conversion --exclude=wn-dc-15 --export=ALL,CONFIG_PATH=\"$PWD/$CONFIG_FILE\" --array=1-$N \"$SCRIPT_PATH/convert_job_array.sh\""
```

After editing `slurm_client.py` in the running container, restart the biomeroworker service so the change takes effect:

```bash
sudo docker compose restart biomeroworker
```
