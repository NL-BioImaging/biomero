#!/bin/bash

##############################
#       Job blueprint        #
##############################
# You can override all of these settings on the commandline, e.g. sbatch <this-script> --job-name=newJob

# Give your job a name, so you can recognize it in the queue overview
#SBATCH --job-name=omero-job-$jobname

# Define, how many cpus you need. Here we ask for 4 CPU cores.
#SBATCH --cpus-per-task=4

# Define, how long the job will run in real time. This is a hard cap meaning
# that if the job runs longer than what is written here, it will be
# force-stopped by the server. If you make the expected time too long, it will
# take longer for the job to start. Here, we say the job will take 15 minutes.
#              d-hh:mm:ss
#SBATCH --time=00:45:00

# How much memory you need.
# --mem will define memory per node
#SBATCH --mem=5GB

# Define a name for the logfile of this job. %j will add the 'j'ob ID variable
# Use append so that we keep the old log when we requeue this job
# We use omero, so that we can recognize them from Omero job code
#SBATCH --output=omero-%j.log
#SBATCH --open-mode=append

# Turn on mail notification. There are many possible self-explaining values:
# NONE, BEGIN, END, FAIL, ALL (including all aforementioned)
# For more values, check "man sbatch"
#SBATCH --mail-type=END,FAIL

# You may not place any commands before the last SBATCH directive

##############################
#       Job script 	         #
##############################

# Std out will get parsed into the logfile, so it is useful to log all your steps and variables
echo "Running $jobname Job w/ $IMAGE_PATH | $SINGULARITY_IMAGE | $DATA_PATH | $SCRIPT_PATH | $DO_CONVERT | \
	$PARAMS" 

# Load singularity module if needed
echo "Loading Singularity/Apptainer..."
module load singularity || true

# Convert datatype if needed
echo "Preprocessing data..."
if $DO_CONVERT; then
    # Generate a unique config file name using job ID
    CONFIG_FILE="config_${SLURM_JOB_ID}.txt"

    # Find all .zarr files and generate a config file
    find "$DATA_PATH/data/in" -name "*.zarr" | awk '{print NR, $0}' > "$CONFIG_FILE"

    # Get the total number of .zarr files
    N=$(wc -l < "$CONFIG_FILE")
    echo "Number of .zarr files: $N"

    # Submit the conversion job array and wait for it to complete
    sbatch --job-name=conversion --export=ALL,CONFIG_PATH="$PWD/$CONFIG_FILE" --array=1-$N --wait $SCRIPT_PATH/convert_job_array.sh

    # Remove the config file after the conversion is done
    rm "$CONFIG_FILE"
fi
# if $DO_CONVERT; then
# 	# TODO: parallel? submit to the slurm again? srun? sbatch --wait
# 	N = $(find "$DATA_PATH/data/in" -name "*.zarr" | wc -l)
# 	echo "$N"
# 	# sbatch --job-name=conversion --array=1-$N --wait convert_job_array.sh
# 	sbatch --job-name=conversion --wait convert_job_array.sh

# 	# sequential
# 	# find $DATA_PATH/data/in -name "*.zarr" -exec singularity run $CONVERSION_PATH/$CONVERTER_IMAGE {} \;

# 	# Remove the zarrs as input
# 	# rm -rf $DATA_PATH/data/in/*.zarr
# fi

# We run a (singularity) container with the provided ENV variables.
# The container is already downloaded as a .simg file at $IMAGE_PATH.
echo "Running workflow..."
singularity run --nv $IMAGE_PATH/$SINGULARITY_IMAGE \
	--infolder $DATA_PATH/data/in \
	--outfolder $DATA_PATH/data/out \
	--gtfolder $DATA_PATH/data/gt \
	--local \
	$PARAMS \
	-nmc && echo "Job completed successfully."

