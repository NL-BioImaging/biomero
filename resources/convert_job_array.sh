#!/bin/bash
#SBATCH --job-name=conversion
#SBATCH --array=1-1
#SBATCH -n 1
#SBATCH -N 1
#SBATCH --time=00:20:00

# Log important values
echo "Job Parameters:"
echo "CONFIG_PATH: $CONFIG_PATH"
echo "DATA_PATH: $DATA_PATH"
echo "CONVERSION_PATH: $CONVERSION_PATH"
echo "CONVERTER_IMAGE: $CONVERTER_IMAGE"

# Load any necessary modules or set environment variables
echo "Loading Singularity/Apptainer..."
module load singularity || true

# Extract the input file for the current SLURM_ARRAY_TASK_ID
file_to_convert=$(awk -v ArrayTaskID=$SLURM_ARRAY_TASK_ID '$1==ArrayTaskID { $1=""; print substr($0,2) }' "$CONFIG_PATH")

# Log the current task and the corresponding input file
echo "Processing task $SLURM_ARRAY_TASK_ID: $file_to_convert"

# Check if the input file exists
if [ -e "$file_to_convert" ]; then
    # Log the conversion process
    echo "Starting conversion for task $SLURM_ARRAY_TASK_ID..."

    # Run the conversion with default parameters
    if singularity run $CONVERSION_PATH/$CONVERTER_IMAGE "$file_to_convert"; then
        # Remove the original file/folder only if conversion succeeded
        rm -rf "$file_to_convert"
        echo "Task $SLURM_ARRAY_TASK_ID completed successfully."
    else
        echo "ERROR: Conversion failed for task $SLURM_ARRAY_TASK_ID. Input file NOT deleted."
        exit 1
    fi
else
    # Log if no corresponding input file is found
    echo "No corresponding input file for task $SLURM_ARRAY_TASK_ID."
    exit 1
fi