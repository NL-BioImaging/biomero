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
# Note that we use the timeout command in the actual script to requeue the job.
#SBATCH --time=00:15:00

# Define, if and what GPU your job requires. 
#SBATCH --gres=gpu:1g.10gb:1

# How much memory you need.
# --mem will define memory per node
#SBATCH --mem=5GB

# Define a name for the logfile of this job. %4j will add the 'j'ob ID variable
# Use append so that we keep the old log when we requeue this job
# We use omero, so that we can recognize them from Omero job code
#SBATCH --output=omero-%4j.log
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
echo "Running $jobname Job w/ $IMAGE_PATH | $SINGULARITY_IMAGE | $DATA_PATH | \
	$PARAMS" 

# We run a (singularity) container with the provided ENV variables.
# The container is already downloaded as a .simg file at $IMAGE_PATH.
singularity run --nv $IMAGE_PATH/$SINGULARITY_IMAGE \
	--infolder $DATA_PATH/data/in \
	--outfolder $DATA_PATH/data/out \
	--gtfolder $DATA_PATH/data/gt \
	--local \
	$PARAMS \
	-nmc && echo "Job completed successfully."

