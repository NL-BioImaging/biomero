#!/bin/bash
#SBATCH --job-name=conversion
#SBATCH --array=1-N

# Log important values
echo "Job Parameters:"
echo "CONFIG_PATH: $CONFIG_PATH"
echo "DATA_PATH: $DATA_PATH"
echo "CONVERSION_PATH: $CONVERSION_PATH"
echo "CONVERTER_IMAGE: $CONVERTER_IMAGE"

# Load any necessary modules or set environment variables
echo "Loading Singularity/Apptainer..."
module load singularity || true

# Extract the .zarr file for the current SLURM_ARRAY_TASK_ID
zarr_file=$(awk -v ArrayTaskID=$SLURM_ARRAY_TASK_ID '$1==ArrayTaskID {print $2}' $CONFIG_PATH)

# Set the full path to the zarr file
file_to_convert="$DATA_PATH/data/in/$zarr_file"

# Log the current task and the corresponding zarr file
echo "Processing task $SLURM_ARRAY_TASK_ID: $file_to_convert"

# Check if the zarr file exists
if [ -e "$file_to_convert" ]; then
    # Log the conversion process
    echo "Starting conversion for task $SLURM_ARRAY_TASK_ID..."

    # Run the conversion
    singularity run $CONVERSION_PATH/$CONVERTER_IMAGE "$file_to_convert"

    # Remove the original zarr file after conversion
    rm "$file_to_convert"

    # Log the completion of the task
    echo "Task $SLURM_ARRAY_TASK_ID completed successfully."
else
    # Log if no corresponding zarr file is found
    echo "No corresponding .zarr file for task $SLURM_ARRAY_TASK_ID."
fi