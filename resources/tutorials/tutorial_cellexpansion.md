# CellExpansion tutorial

## Introduction

Different type of aggregates of proteins can form inside a nucleus or inside the cytoplasm of a cell.
In our example, we have aggregates (spots) outside of the nucleus and we want to quantify these per cell.

## 1. Import data to OMERO

Import [data](./images/Cells.tif) as you would normally.

We use this image 'Cells.tif', shown as part of this png with a mask here:

![Nuclei label image](https://github.com/NL-BioImaging/omero-slurm-client/blob/502dd074e995b29d5206056d0f9c6eae0a3450b4/resources/tutorials/images/nuclei_labels.png?raw=true)

## 2. Extract masks with Cellpose

This process is actually 2 steps: we want the nuclei masks and also the aggregates masks.
Luckily these were stained with different colors and are available in different channels:
- Channel 3 = Nuclei
- Channel 2 = Aggregates

So we can run 2 CellPose workflows on OMERO and retrieve both masks.
We store them as images in a new dataset and particularly name them: "{original_file}NucleiLabels.{ext}"  and "{original_file}GranulesLabels.{ext}".

Combine both in the same dataset afterward, this will be our input dataset for the CellExpansion algorithm.

## 3. CellExpansion

To estimate the amount of aggregates per cell, we actually need the cytoplasm in our example. Then we can calculate overlap. 

One could segment the cytoplasm, especially in this image (its just channel 1), but we have a Python script that does this algorithmically instead for the fun of it.

We apply the CellExpansion algorithm on the nuclei mask and estimate the full reach of the cells with new masks.

![4 images showing cell expansion](https://github.com/NL-BioImaging/omero-slurm-client/blob/502dd074e995b29d5206056d0f9c6eae0a3450b4/resources/tutorials/images/cellexpansion.png?raw=true)

For this, we have to first add it to OMERO: 
We could just add the Python code to a OMERO job script. But then the Processor needs to have the right Python libraries installed. 
Instead, we should package it in a lightweight container with the correct Python environment. This in turn makes the workflow more FAIR.

1. I made this workflow container for it: [github repo](https://github.com/TorecLuik/W_CellExpansion).
2. Release a version and publish a [docker image](https://hub.docker.com/layers/torecluik/w_cellexpansion/v1.0.1/images/sha256-8d2f9e663614588f11f41c09375568b448b6d158478a968dac23dbbd8d7fdebc?context=explore)
2. Add the workflow to Slurm and OMERO:
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

4. Run the workflow on our Nuclei mask.
Output the new mask back as image in a new dataset.


## Calculate overlap

We calculate overlap with another very short Python script.
It outputs the overlap counts of 2 masks. 

Example original code:
```Python
imCellsCellLabels=imread('images/CellsNucleiLabels.tif',cv2.IMREAD_ANYDEPTH)
imCellsGranulesLabels=imread('images/CellsGranulesLabels.tif',cv2.IMREAD_ANYDEPTH)
numCells=np.max(imCellsCellLabels)
CellNumGranules=np.zeros([numCells,2],dtype=np.int16)
granulesStats=pd.DataFrame(measure.regionprops_table(imCellsGranulesLabels, properties=('centroid',)))
granulesStatsnp=np.ndarray.astype(np.round(granulesStats.to_numpy()),dtype=np.uint16)
granulesStatsInCellLabel=imCellsCellLabels[granulesStatsnp[:,0],granulesStatsnp[:,1]]
for i in range(1,numCells+1):
    CellNumGranules[i-1,0]=np.count_nonzero(granulesStatsInCellLabel==i)
pd.DataFrame(CellNumGranules,columns=['Estimated']).style
```

I added this as a separate workflow at [W_CountMaskOverlap](https://github.com/TorecLuik/W_CountMaskOverlap).

1. add the workflow to config. 
2. make one dataset with pairs of our mask files. We name them the same as the original image, but with an extra suffix. E.g. Cells_CellExpansion.tif and Cells_Aggregates.tif. 
3. Call the new workflow on this dataset / image selection, and supply the suffixes chosen ("_CellExpansion" and "_Aggregates") as parameter. Then make sure to upload the result of the workflow as a zip, as it will be a csv file.
4. Check the resulting csv for a count of aggregates per cell!

## Workflow management?

Of course, this required knowledge and manual manipulation of renaming images and supplying that metadata to the next workflow. Ideally you would be able to string singular workflows together with Input/Output like using NextFlow or Snakemake. We are looking into it for a future version.


## Extra

### Out of memory

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