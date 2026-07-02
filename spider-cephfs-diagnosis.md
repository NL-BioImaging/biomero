# Diagnosis of Slurm Job Failures on SURF Spider Cluster

## Executive Summary

The primary cause of the **conversion step failures** is a storage mount mismatch on the Spider cluster. The CephFS project storage `/project/biomero` is **not mounted** on several compute nodes. Failing nodes: `wn-dc-15` (24 failures), `wn-la-14` (3 failures), `wn-dc-08` (1 failure), `wn-dc-11` (1 failure). The missing mount was **directly confirmed** on `wn-dc-15` and `wn-la-14` via probe jobs; `wn-dc-08` and `wn-dc-11` are inferred from identical failure signatures. The pattern is not by node prefix — `wn-dc-07` and `wn-dc-09` work fine, while `wn-la-14` fails. The mount is missing on specific nodes regardless of their naming.

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

### Unmounted `/project/biomero` on Specific Nodes (Conversion Failure)

* **Symptom**: Conversion jobs fail with exit code `1:0` and the error:
  ```text
  No corresponding input file for task X.
  ```
  This is the `convert_job_array.sh` error branch triggered when `[ -e "$file_to_convert" ]` is false — i.e. the input `.zarr` path does not exist on the executing node.

* **Statistical evidence** (`sacct` over full day, 2026-06-29):

  | Node | Result | Count | Mount status |
  |------|--------|-------|--------------|
  | `wn-dc-15` | FAILED 1:0 | 24 | NOT mounted (probed: job 37069917) |
  | `wn-la-14` | FAILED 1:0 | 3 | NOT mounted (probed: job 37075309) |
  | `wn-dc-08` | FAILED 1:0 | 1 | inferred (probe queued: job 37075310, PENDING as of 2026-06-29) |
  | `wn-dc-11` | FAILED 1:0 | 1 | inferred (probe queued: job 37075311, PENDING as of 2026-06-29) |
  | `wn-la-09` | COMPLETED 0:0 | 23 | mounted |
  | `wn-la-18` | COMPLETED 0:0 | 5 | mounted |
  | `wn-la-07` | COMPLETED 0:0 | 3 | mounted |
  | `wn-dc-07` | COMPLETED 0:0 | 3 | mounted |
  | `wn-dc-09` | COMPLETED 0:0 | 1 | mounted |

  The pattern is **not** strictly `wn-dc-*` vs `wn-la-*`: `wn-dc-07` and `wn-dc-09` work fine, while `wn-la-14` also fails. The common factor is the **specific nodes** where `/project/biomero` is not mounted, regardless of node prefix.

  Within the same job arrays, tasks on working nodes completed while tasks on the same array scheduled to `wn-dc-15` failed — confirming the failure is node-specific, not data-specific.

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

  A second probe pinned to `wn-la-14` (job `37075309`, `COMPLETED 0:0`) showed the same result:
  ```
  NODE=wn-la-14.spider.surfsara.nl
  ls: cannot access '/project/biomero': No such file or directory
  NOT_MOUNTED
  ```
  This confirms the failure is tied to the missing mount, not to the node's partition or naming.

---

## Suggested Solutions

### Permanent Solution (Recommended)

**Contact SURF Spider Support**: Ask the SURF administrators to mount the CephFS volume `/project/biomero` on **all `normal` partition compute nodes**.

Relevant details for the support ticket:
- Project: `biomero` (CephFS at `/project/biomero`)
- Slurm user: `biomero-sloev`
- Affected nodes (from `sacct` failure signatures): `wn-dc-15`, `wn-la-14`, `wn-dc-08`, `wn-dc-11`
- Working nodes: `wn-la-07`, `wn-la-09`, `wn-la-18`, `wn-dc-07`, `wn-dc-09`
- Mount probes (confirm `/project/biomero` absent + `NOT_MOUNTED`): job `37069917` on `wn-dc-15`, job `37075309` on `wn-la-14`
- Pending probes (queued, not yet run as of 2026-06-29 — check later to upgrade `wn-dc-08`/`wn-dc-11` from inferred to confirmed): job `37075310` on `wn-dc-08`, job `37075311` on `wn-dc-11`. Control probes (expected `mounted`): job `37075312` on `wn-dc-07`, job `37075313` on `wn-dc-09`.

  To check later: `sacct -j 37075310,37075311,37075312,37075313 --format=JobID,JobName%14,NodeList,State,ExitCode` and read the `~/probe-*-<jobid>.out` files.

### Temporary Workaround (BIOMERO Configuration)

If SURF cannot fix this immediately, exclude the problematic node(s) by modifying the conversion command in `biomero/slurm_client.py` (in `get_conversion_command`, around line 2385):

```python
conversion_cmd = "sbatch --job-name=conversion --exclude=wn-dc-15,wn-la-14,wn-dc-08,wn-dc-11 --export=ALL,CONFIG_PATH=\"$PWD/$CONFIG_FILE\" --array=1-$N \"$SCRIPT_PATH/convert_job_array.sh\""
```

After editing `slurm_client.py` in the running container, restart the biomeroworker service so the change takes effect:

```bash
sudo docker compose restart biomeroworker
```
