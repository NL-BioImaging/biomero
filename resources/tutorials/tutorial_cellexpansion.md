# CellExpansion tutorial

## Introduction

Different type of aggregates of proteins can form inside a nucleus or inside the cytoplasm of a cell.
In our example, we have aggregates (spots) outside of the nucleus and we want to quantify these per cell.

## 1. Import data to Omero

Import [data](./images/Cells.tif) as you would normally.

We use this image, shown as part of this png with a mask here:

![Nuclei label image](https://github.com/NL-BioImaging/omero-slurm-client/blob/502dd074e995b29d5206056d0f9c6eae0a3450b4/resources/tutorials/images/nuclei_labels.png?raw=true)

## 2. Extract masks with Cellpose

This process is actually 2 steps: we want the nuclei masks and also the aggregates masks.
Luckily these were stained with different colors and are available in different channels:
- Channel 3 = Nuclei
- Channel 2 = Aggregates

So we can run 2 CellPose workflows on Omero and retrieve both masks.
We store them as images in a new dataset and particularly name them: "{original_file}NucleiLabels.{ext}"  and "{original_file}GranulesLabels.{ext}".

Combine both in the same dataset afterward, this will be our input dataset for the CellExpansion algorithm.

## 3. CellExpansion

To estimate the amount of aggregates per cell, we actually need the cytoplasm in our example. Then we can calculate overlap. 

One could segment the cytoplasm possibly, but we have a Python script that does this algorithmically instead.

We apply the CellExpansion algorithm on the nuclei mask and estimate the full reach of the cells with new masks.

![4 images showing cell expansion](https://github.com/NL-BioImaging/omero-slurm-client/blob/502dd074e995b29d5206056d0f9c6eae0a3450b4/resources/tutorials/images/cellexpansion.png?raw=true)

For this, we have to first add it to Omero: 
We could just add the Python code to a Omero job script. But then the Processor needs to have the right Python libraries installed. 
Instead, we should package it in a lightweight container with the correct Python environment. This in turn makes the workflow more FAIR.

1. I made this workflow container for it: [github repo](https://github.com/TorecLuik/W_CellExpansion).
2. Release a version and publish a [docker image](https://hub.docker.com/layers/torecluik/w_cellexpansion/v1.0.1/images/sha256-8d2f9e663614588f11f41c09375568b448b6d158478a968dac23dbbd8d7fdebc?context=explore)
2. Add the workflow to Slurm and Omero:
```ini
# -------------------------------------
# CELLEXPANSION SPOT COUNTING
# -------------------------------------
# The path to store the container on the slurm_images_path
cellexpansion=cellexpansion
# The (e.g. github) repository with the descriptor.json file
cellexpansion_repo=https://github.com/TorecLuik/W_CellExpansion/tree/v1.0.1
# The jobscript in the 'slurm_script_repo'
cellexpansion_job=jobs/cellexpansion.sh
```

3. Run the workflow on our Nuclei mask:

For this, we need to rename our mask files first. 

## Calculate overlap

We calculate overlap with another very short Python script, which I added to the `wrapper.py` of the cellexpansion workflow.
It outputs a `.csv` file with the counts.

# Extra

## Out of memory

While running CellPose on the Aggregates, my job ran out of memory. So I had to bump up the default memory used by the generated job scripts, in `slurm_config.ini`:

```ini
# -------------------------------------
# CELLPOSE SEGMENTATION
# -------------------------------------
# The path to store the container on the slurm_images_path
cellpose=cellpose
# The (e.g. github) repository with the descriptor.json file
cellpose_repo=https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose/tree/v1.2.7
# The jobscript in the 'slurm_script_repo'
cellpose_job=jobs/cellpose.sh
# Override the default job values for this workflow
# Or add a job value to this workflow
# If you don't want to override, comment out / delete the line.
# Run CellPose Slurm with 10 GB GPU
cellpose_job_gres=gpu:1g.10gb:1
# Run CellPose Slurm with 15 GB CPU memory
cellpose_job_mem=15GB
```
I added the `...mem=15GB` configuration, which will add `mem=15GB` to the Slurm job command from now on for CellPose workflows. 
No need to restart the server, these changes get picked up whenever we start a new client from this config file (which is when we start a new script).

So after updating that `ini` file, I kickstart the workflow for channel 2 again and this time it works and returns the mask.