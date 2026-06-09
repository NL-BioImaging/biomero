#!/bin/bash
#SBATCH --job-name=cellexpansion
#SBATCH --cpus-per-task=4
#SBATCH --time=00:15:00
#SBATCH --mem=5GB
#SBATCH --output=omero-%4j.log

echo "Running CellExpansion w/ $IMAGE_PATH | $SINGULARITY_IMAGE | $DATA_PATH | $MAX_PIXELS $DISCARD_CELLS_WITHOUT_CYTOPLASM"

GPU_FLAG=""
case "${USE_GPU:-}" in
  true|True|TRUE|1|yes|Yes|YES|y|Y|on|On|ON) GPU_FLAG="--nv" ;;
esac

singularity run $GPU_FLAG $IMAGE_PATH/$SINGULARITY_IMAGE \
    --infolder $DATA_PATH/data/in \
    --outfolder $DATA_PATH/data/out \
    --gtfolder $DATA_PATH/data/gt \
    --local \
    --max_pixels $MAX_PIXELS \
    --discard_cells_without_cytoplasm $DISCARD_CELLS_WITHOUT_CYTOPLASM \
    -nmc
