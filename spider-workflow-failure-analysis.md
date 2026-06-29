# Workflow-Specific Failure Analysis (Internal)

> **Scope**: Failures *inside* the analysis workflow containers — wrapper bugs, model/parameter mismatches, or input-metadata issues. These are **not** HPC/infrastructure problems and **not** relevant to SURF Spider support. They are ours to fix in the workflow images.
>
> Distinguish from the conversion/mount failures in [spider-cephfs-diagnosis.md](spider-cephfs-diagnosis.md) (CephFS mount missing on certain nodes).

All workflow jobs log to `omero-<JOBID>.log` in the Slurm user home (`/home/biomero-sloev/`), run on `wn-ga-01` (`gpu_a100_mig`), and exit with Slurm `ExitCode 2:0` — the job *ran*, but the workflow reported an application-level error. The `2:0` is the workflow's own non-zero exit, not a node/infra fault.

**Key finding — different jobs of the *same* workflow fail for different reasons.** A single root-cause claim per workflow is wrong. In particular there are **two different cellpose images** in play:
* `w_nucleisegmentation-cellpose_v1.4.0.sif` — old **cellpose** (`nuclei` model, CPU).
* `w_segmentation-cellpose4_v0.10.1.sif` — new **segmentation_cellpose4** (`cpsam` model, GPU).

These two images fail in completely different ways, and even within the cpsam image the underlying crash differs per input. Every job below is mapped to its **actual log line**.

---

## Per-Job Evidence (`sacct` + `omero-<job>.log`, 2026-06-29)

| Job ID | Workflow / image | Input | Underlying error (verbatim) |
|--------|------------------|-------|------------------------------|
| `37066428` | deconvolve | DividingCellcrop | `IndexError: list index out of range for attribute 'ExcitationWavelength'` |
| `37066446` | deconvolve | DividingCellcrop | same `ExcitationWavelength` IndexError |
| `37066456` | deconvolve | test3 — **2 items**: DividingCellcrop + `Test-subs_copies.lif [image2]` | same `ExcitationWavelength` IndexError (both items) |
| `37066437` | cellpose v1.4.0 (`nuclei`, CPU) | Test_1 | `ValueError: ...shape == (..,..,3), got (512, 512, 2)` in `rgb2gray` |
| `37067006` | cellpose v1.4.0 (`nuclei`, CPU) | FluoCells | same `rgb2gray` `(512, 512, 2)` ValueError |
| `37066251` | cellpose v1.4.0 (`nuclei`, CPU) | XW01_GPCR plate (only a `.zarr` item) | `IsADirectoryError: ... .ome.tif.zarr` |
| `37065285` | cellpose4 (`cpsam`, GPU) | XW01_GPCR plate, `--diameter 0`, time-series 19 slices | cellpose exit 1 → `ZeroDivisionError: float division by zero` |
| `37066246` | cellpose4 (`cpsam`, GPU) | DS1, `--do_3D=True`, z=26 | cellpose exit 1 → `AttributeError: 'NoneType' object has no attribute 'squeeze'` + secondary `IsADirectoryError: 3.ome.tiff.zarr` |
| `37066310` | cellpose4 (`cpsam`, GPU) | Cell-Granules (1824×2736), `--augment --bsize 512` | `RuntimeError: size of tensor a (64) must match b (32) at dim 2` + secondary `IsADirectoryError: Cell-Granules.tif.zarr` |
| `37067017` | cellpose4 (`cpsam`, GPU) | Cell-Granules, `--augment --bsize 512` | same tensor `64 vs 32` mismatch |
| `37067015` | cellpose4 (`cpsam`, GPU) | `Test-subs_copies.lif [imagecopy_000]` (name has spaces/brackets) | `ValueError: ERROR: no files in --dir folder` |
| `37067031` | aggregates (CellProfiler) | Test_1 | `ValueError: Failed to execute the CellProfiler pipeline ... (return code: 1)` |
| `37067041` | aggregates (CellProfiler) | Test_1 | same CellProfiler `return code: 1` |

---

## Per-Job Deep Dive

### Deconvolve — `37066428`, `37066446`, `37066456`

```text
ERROR processing DividingCellcrop.ome.tiff.zarr: list index out of range for attribute 'ExcitationWavelength'
Traceback (most recent call last):
    raise IndexError(msg) from exc
IndexError: list index out of range for attribute 'ExcitationWavelength'
CIDeconvolve workflow completed with errors: N failed item(s).
ERROR: Workflow completed without producing files in .../data/out
```
* When writing the output OME-TIFF, `tifffile.imwrite` maps per-channel metadata (`ExcitationWavelength`) onto image channels. The metadata array has fewer entries than the channel count → index overrun.
* `37066456` reproduced it on **two different inputs** (`DividingCellcrop` and `Test-subs_copies.lif [image2]`), so it is input-class-wide, not a one-off file.

### Cellpose v1.4.0 (`nuclei`, CPU) — rgb2gray — `37066437`, `37067006`

```text
img = skimage.color.rgb2gray(img) * 255
File ".../python3.7/site-packages/skimage/color/colorconv.py", line 804, in rgb2gray
    raise ValueError(msg)
ValueError: the input array must be have a shape == (.., ..,[ ..,] 3)), got (512, 512, 2)
```
* The old wrapper unconditionally calls `skimage.color.rgb2gray(img)`; the input is **2-channel** `(512, 512, 2)`. `rgb2gray` requires 3/4 channels → ValueError. Run with `--cp_model=nuclei --use_gpu=false`.

### Cellpose v1.4.0 (`nuclei`, CPU) — `.zarr` as file — `37066251`

```text
Traceback (most recent call last):
IsADirectoryError: [Errno 21] Is a directory:
  '.../data/in/XW01_GPCR_R2_plate700.companion.ome [Well A1, Field #1].ome.tif.zarr'
```
* The only input item was a `.zarr` **directory**; the wrapper opens it as a flat file → `IsADirectoryError`. Same bug recurs as a *secondary* error in the cpsam jobs below.

### Cellpose4 (`cpsam`, GPU) — `ZeroDivisionError` — `37065285`

```text
Running Cellpose with command: conda run -n cellpose_env cellpose ... --diameter 0 ...
Traceback (most recent call last):
ZeroDivisionError: float division by zero
Status: 2 - 'Error processing XW01_GPCR_R2_plate700.0.tif: Cellpose failed with exit code 1'
```
* Run with `--diameter 0` on a 19-slice time series. The exit-1 subprocess crash is actually a `ZeroDivisionError` (diameter/scale division by zero), not a tensor or rgb issue.

### Cellpose4 (`cpsam`, GPU) — `NoneType.squeeze` (3D) — `37066246`

```text
Running Cellpose ... --diameter 30 ... --do_3D
Status: 4 - 'Cellpose failed with exit code 1: Traceback (most recent call last):
AttributeError: 'NoneType' object has no attribute 'squeeze'
Status: 2 - 'Error processing 3.ome.tiff.zarr: [Errno 21] Is a directory: '.../3.ome.tiff.zarr''
```
* `--do_3D=True` (z=26) crashes with `AttributeError: 'NoneType' object has no attribute 'squeeze'` (model returned `None`). A second item (`3.ome.tiff.zarr`) then hits the same `.zarr`-as-file `IsADirectoryError`.

### Cellpose4 (`cpsam`, GPU) — tensor mismatch — `37066310`, `37067017`

```text
Large image detected (1824x2736), using tiling with bsize=512
Running Cellpose ... --augment --bsize 512 ...
Traceback (most recent call last):
RuntimeError: The size of tensor a (64) must match the size of tensor b (32) at non-singleton dimension 2
```
* Both runs use `--augment --bsize 512` on a large image that triggers tiling. The cpsam model produces mismatched feature-map sizes (`64` vs `32`) → `RuntimeError`. `37066310` also hits the secondary `Cell-Granules.tif.zarr` `IsADirectoryError`.

### Cellpose4 (`cpsam`, GPU) — no files in `--dir` — `37067015`

```text
Running Cellpose ... --dir .../tmp_Test-subs_copies.lif [imagecopy_000].0_1782736876 ...
    raise ValueError("ERROR: no files in --dir folder ")
ValueError: ERROR: no files in --dir folder
```
* The input filename contains **spaces and brackets** (`Test-subs_copies.lif [imagecopy_000]`). The temp `--dir` built from it ends up empty / unmatched, so cellpose finds no files. Likely a filename-quoting / glob bug in the wrapper, not a real empty input.

### Aggregates / CellProfiler — `37067031`, `37067041`

```text
Progress: 50 % ... Status: - 'Failed to execute the CellProfiler pipeline: cellprofiler -c -r -p .../FullMeasurementsNucleiCellAggregates.cppipe -i .../data/in -o .../data/out -t ... (return code: 1)'
Traceback (most recent call last):
    raise ValueError(err_desc)
ValueError: Failed to execute the CellProfiler pipeline: ... (return code: 1)
ERROR: Workflow completed without producing files in .../data/out
```
* The `cellprofiler -c -r -p FullMeasurementsNucleiCellAggregates.cppipe` subprocess exits non-zero at ~50%. The wrapper only surfaces the return code; the actual CellProfiler module error is **not** captured in `omero-<job>.log` (would need CellProfiler's own `-L`/stderr output to root-cause).

---

## Summary per Workflow (detected errors + short fixes)

### 1. Deconvolve (`CIDeconvolve`)
* **Detected:** `IndexError: list index out of range for attribute 'ExcitationWavelength'` on OME-TIFF write — every deconvolve job (`37066428/46/56`).
* **Fix (short):** pad/trim per-channel metadata to the channel count before `tifffile.imwrite` (or catch & write minimal OME-XML).

### 2. Cellpose v1.4.0 — old `nuclei` image (CPU)
* **Detected:**
  * 2-channel `rgb2gray` `ValueError ... got (512, 512, 2)` (`37066437`, `37067006`).
  * `.zarr` directory read as a file → `IsADirectoryError` (`37066251`).
* **Fix (short):** only `rgb2gray` when ≥3 channels (else pick/duplicate a channel); skip/handle `.zarr` directories in the input lister.

### 3. Cellpose4 — new `cpsam` image (GPU)
* **Detected — four distinct underlying crashes:**
  * `ZeroDivisionError: float division by zero` with `--diameter 0` (`37065285`).
  * `AttributeError: 'NoneType' object has no attribute 'squeeze'` with `--do_3D=True` (`37066246`).
  * `RuntimeError: size of tensor a (64) must match b (32)` with `--augment --bsize 512` tiling on large images (`37066310`, `37067017`).
  * `ValueError: ERROR: no files in --dir folder` for filenames with spaces/brackets (`37067015`).
  * Recurring **secondary** `.zarr`-as-file `IsADirectoryError` (`37066246`, `37066310`).
* **Fix (short):** avoid `--diameter 0` (or guard the divide); review the 3D path returning `None`; revisit `--augment`/`--bsize 512` tiling for the cpsam model; quote/sanitize temp `--dir` paths; exclude `.zarr` dirs from the input list.

### 4. Aggregates (CellProfiler)
* **Detected:** `ValueError: Failed to execute the CellProfiler pipeline ... (return code: 1)` at ~50% (`37067031`, `37067041`). Underlying CellProfiler module error not captured.
* **Fix (short):** capture/forward CellProfiler stderr (`-L` log) so the failing module is visible, then diagnose.

---

## Cross-check vs. the Other AI's Report

| Other AI claim | Verdict | Evidence |
|----------------|---------|----------|
| Conversion failures = `/project/biomero` not mounted on certain nodes | ✅ Correct (see HPC doc); their node list (`wn-dc-08`, `wn-dc-15`) is **incomplete** — also `wn-la-14`, `wn-dc-11` | probes `37069917`, `37075309` |
| Cellpose crash = 2-channel `rgb2gray` | ✅ Correct **only for the old v1.4.0 image** (`37066437`, `37067006`) — not the whole story | logs |
| Deconvolve crash = `ExcitationWavelength` IndexError | ✅ Correct | `37066428/46/56` |
| (missing) cpsam `ZeroDivisionError` (`--diameter 0`) | ❌ Omitted | `37065285` |
| (missing) cpsam `NoneType.squeeze` (`--do_3D`) | ❌ Omitted | `37066246` |
| (missing) cpsam tensor `64 vs 32` (`--augment --bsize 512`) | ❌ Omitted | `37066310`, `37067017` |
| (missing) cpsam `no files in --dir` (filename spaces/brackets) | ❌ Omitted | `37067015` |
| (missing) `.zarr` IsADirectoryError | ❌ Omitted | `37066251` (+ secondary in 246/310) |
| (missing) CellProfiler aggregates failure | ❌ Omitted | `37067031`, `37067041` |

**Conclusion:** Your hypothesis was right — these are *different jobs of the same workflow* failing differently. The other AI's three findings are each valid for *some* jobs but collapsed a multi-cause situation into one cause per workflow. There are really **two cellpose images**, and the `cpsam` image alone has **four** distinct underlying crashes plus a recurring `.zarr` wrapper bug. Their conversion diagnosis is correct but their bad-node list is incomplete.
