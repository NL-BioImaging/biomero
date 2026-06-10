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
# take longer for the job to start.
# Here, we say the job will get a timeout after 45 minutes.
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

BIOMERO_ENV_FILE="${1:-}"
if [ -n "$BIOMERO_ENV_FILE" ] && [ -f "$BIOMERO_ENV_FILE" ]; then
    . "$BIOMERO_ENV_FILE"
fi

##############################
#       Job script           #
##############################

echo "Running $jobname Job w/ $IMAGE_PATH | $SINGULARITY_IMAGE | $DATA_PATH | $SCRIPT_PATH | $DO_CONVERT | \
	$PARAMS"

echo "Loading Singularity/Apptainer if needed..."
module load singularity > /dev/null 2>&1 || true

echo "Running bilayers workflow..."
singularity run $GPU_FLAG "$IMAGE_PATH/$SINGULARITY_IMAGE" \
	$INPARAMS \
	$OUTPARAMS \
	$PARAMS \
	&& echo "Job completed successfully."
