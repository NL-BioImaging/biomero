# Google Cloud Slurm tutorial

## Introduction

This library is meant to be used with some external HPC cluster using Slurm, to offload your (Omero) compute to servers suited for it.

However, if you don't have ready access (yet) to such a cluster, you might want to spin some test environment up in the Cloud and connect your (local) Omero to it. 
This is what we will cover in this tutorial, specifically Google Cloud.

## 0. Requirements

To follow this tutorial, you need:
- Git
- Docker
- Omero Insight
- A creditcard (but we'll work with free credits)

I use Windows here, but it should work on Linux/Mac too. If not, let me know.

I provide ready-to-go TL;DR, but in the details of each chapter I walk through the steps I took to make these containers ready.


## 1. Setup Google Cloud for Slurm

### TL;DR:
1. Follow this [tutorial](https://cloud.google.com/hpc-toolkit/docs/quickstarts/slurm-cluster) from Google Cloud. Click 'guide me'.
2. Make a new Google Account to do this, with free $300 credits to use Slurm for a bit. This requires the creditcard (but no cost).


<details>
  <summary>Details</summary>

So, we follow this [tutorial](https://cloud.google.com/hpc-toolkit/docs/quickstarts/slurm-cluster) and end up with a `hpcsmall` VM on Google Cloud.

</details>

However, we are missing an ingredient: SSH access!

## 2. Add SSH access

### TL;DR:
1. Add your public SSH key (`~/.ssh/id_rsa.pub`) to the Google Cloud instance, like [here](https://cloud.google.com/compute/docs/connect/add-ssh-keys?cloudshell=true#gcloud).
2. Turn the [firewall](https://console.cloud.google.com/net-security/firewall-manager/firewall-policies/list) setting (e.g. `hpc-small-net-fw-allow-iap-ingress`) to allow `0.0.0.0/0` as IP ranges for `tcp:22`.
3. Promote the login node's IP address to a static one: [here](https://cloud.google.com/compute/docs/ip-addresses/reserve-static-external-ip-address#promote_ephemeral_ip)
3. Copy that IP and your username.
3. On your own computer, add a SSH config file, store it as `~/.ssh/config` (no extension) with the ip and user filled in:

```yaml
Host gcslurm
	HostName <fill-in-the-External-IP-of-VM-instance>
	User <fill-in-your-Google-Cloud-user>
	Port 22
	IdentityFile ~/.ssh/id_rsa
```

<details>
  <summary>Details</summary>

We need to setup our library with SSH access between Omero and Slurm, but this is not built-in to these Virtual Machines yet.
We will forward our local SSH to our Omero (in this tutorial), so we just need to setup SSH access to the Google Cloud VMs.

This sounds easier than it actually is.

Follow the steps at [here](https://cloud.google.com/compute/docs/connect/add-ssh-keys?cloudshell=true#gcloud):

0. Note that this tutorial by default seems to use the "OS Login" method, using the mail account you signed up with.
1. Open a Cloud Shell
2. Upload your public key to this Cloud Shell (with the `...` button).
3. Run the `gcloud compute ...` command they show, pointing at your newly uploaded public key. Leave out the optional `project` and `expire_time`.

Then, we have to ensure that the firewall accepts requests from outside Google Cloud, if it doesn't already. 

Go to the [firewall](https://console.cloud.google.com/net-security/firewall-manager/firewall-policies/list) settings and edit the tcp:22 (e.g. `hpc-small-net-fw-allow-iap-ingress`) and add the `0.0.0.0/0` ip ranges.

Now we are ready:
- `ssh -i ~/.ssh/id_rsa <fill-in-your-Google-Cloud-user>@<fill-in-the-External-IP-of-VM-instance>`

E.g. my Google Cloud user became `t_t_luik_amsterdamumc_nl`, related to the email I signed up with.
The External IP was on the [VM instances](https://console.cloud.google.com/compute/instances) page for the login node `hpcsmall-login-2aoamjs0-001`.

Now to make this connection easy, we need 2 steps:
1. Fix this external IP address, so that it will always be the same
2. Fix a SSH config file for this SSH connection

For 1, we got to [here](https://cloud.google.com/compute/docs/ip-addresses/reserve-static-external-ip-address#promote_ephemeral_ip) and follow the Console steps to promote the IP address to a static IP address. Now back in the `All` screen, your newly named Static IP address should show up. Copy that IP (it should be the same IP as before, but now it will not change when you restart the system)

For 2, On your own computer, add a SSH config file, store it as `~/.ssh/config` (no extension) with the ip and user filled in:

```yaml
Host gcslurm
	HostName <fill-in-the-External-IP-of-VM-instance>
	User <fill-in-your-Google-Cloud-user>
	Port 22
	IdentityFile ~/.ssh/id_rsa
```

Now you should be able to login with a simple: `ssh gcslurm`.


Congratulations!

</details>


## 3. Test Slurm

### TL;DR:
1. SSH into the login node: `ssh gcslurm`
3. Start some filler jobs: `sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname"`
4. Check the progress: `squeue` 
5. Check some output when its done, e.g. job 1: `cat slurm-1.out`

<details>
  <summary>Details</summary>

Now connect via SSH to Google Cloud Slurm and let's see if Slurm works:
```bash
[t_t_luik_amsterdamumc_nl@hpcsmall-login-2aoamjs0-001 ~]$ squeue
             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
[t_t_luik_amsterdamumc_nl@hpcsmall-login-2aoamjs0-001 ~]$ sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname"
Submitted batch job 4
Submitted batch job 5
Submitted batch job 6
Submitted batch job 7
[t_t_luik_amsterdamumc_nl@hpcsmall-login-2aoamjs0-001 ~]$ squeue
             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
                 4     debug     wrap t_t_luik CF       0:03      1 hpcsmall-debug-ghpc-3
                 5     debug     wrap t_t_luik PD       0:00      1 (Resources)
                 6     debug     wrap t_t_luik PD       0:00      1 (Priority)
                 7     debug     wrap t_t_luik PD       0:00      1 (Priority)
```

I fired off 4 jobs that take some seconds, so they are still in the queue by the time I call the `squeue`. Note that the first one might take a while since Google Cloud has to fire up a new compute node for the first time.

The jobs wrote their stdout output in the current dir:
```bash
[t_t_luik_amsterdamumc_nl@hpcsmall-login-2aoamjs0-001 ~]$ ls
slurm-4.out  slurm-5.out  slurm-6.out  slurm-7.out
[t_t_luik_amsterdamumc_nl@hpcsmall-login-2aoamjs0-001 ~]$ squeue
             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
[t_t_luik_amsterdamumc_nl@hpcsmall-login-2aoamjs0-001 ~]$ cat slurm-4.out
hpcsmall-debug-ghpc-3
[t_t_luik_amsterdamumc_nl@hpcsmall-login-2aoamjs0-001 ~]$ cat slurm-5.out
hpcsmall-debug-ghpc-3
```

All on the same node that was spun up, on-demand, by Google Cloud. You should be able to see it still alive in the `VM instances` [tab](https://console.cloud.google.com/compute/instances) as well. It will be destroyed again if not used for a while, saving you costs.

</details>

## 3b. Install Singularity / Apptainer

### TL;DR:
1. Follow this [guide](https://cloud.google.com/architecture/deploying-containerized-workloads-slurm-cluster-compute-engine) to install Singularity in /app


<details>

Now we want to run containers on our Slurm cluster using `singularity`, but this is not installed by default.

Luckily the folks at Google have a [guide](https://cloud.google.com/architecture/deploying-containerized-workloads-slurm-cluster-compute-engine) for it, so let's follow that one.

If the ssh connection to the login node doesn't work from Google Cloud Shell, you can continue with the steps by using the SSH connection (`ssh gcslurm`) that we just built from your local commandline.

Use this URL for the singularity tar:

`https://github.com/apptainer/singularity/releases/download/v3.8.7/singularity-3.8.7.tar.gz`

```bash
wget https://github.com/apptainer/singularity/releases/download/v3.8.7/singularity-3.8.7.tar.gz && tar -xzf singularity-${SINGULARITY_VERSION}.tar.gz && cd singularity-${SINGULARITY_VERSION}
```

The module step did not work for me, so instead we need to make singularity available in the PATH.

```
echo 'export PATH=/apps/singularity/3.8.7/bin:${PATH}' >> ~/.bashrc
```

Follow up with `source ~/.bashrc` and now `singularity --version` should give you `singularity version 3.8.7`.

</details>

Now let's connect Omero to our Slurm!

## 4. Omero & Omero Slurm Client

Ok, now we need a Omero server and a correctly configured Omero Slurm Client.

### TL;DR:
1.  Clone my example `docker-example-omero-grid-amc` locally: `git clone -b processors https://github.com/TorecLuik/docker-example-omero-grid-amc.git`
2. Change the `worker-gpu/slurm-config.ini` file 
    - to point to `gcslurm` profile (or rename your SSH profile to `slurm`)

```ini
[SSH]
# -------------------------------------
# SSH settings
# -------------------------------------
# The alias for the SLURM SSH connection
host=gcslurm
```
    
- and to point to directories relative to the user's home dir:
  
```ini
[SLURM]
# -------------------------------------
# Slurm settings
# -------------------------------------
# General settings for where to find things on the Slurm cluster.
# -------------------------------------
# PATHS
# -------------------------------------
# The path on SLURM entrypoint for storing datafiles
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_data_path=my-scratch/data
# The path on SLURM entrypoint for storing container image files
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_images_path=my-scratch/singularity_images/workflows
# The path on SLURM entrypoint for storing the slurm job scripts
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_script_path=my-scratch/slurm-scripts
```


3. Fire up the Omero containers: `docker-compose up -d --build`
4. Go to Omero.web (`localhost:4080`), login `root` pw `omero`
5. Upload some images (to `localhost`) with Omero.Insight (not included).
6. In web, run the `slurm/init_environment` script

<details>
  <summary>Details</summary>

======= Omero in Docker =======

You can use your own Omero setup, but for this tutorial I will refer to a dockerized Omero that I am working with: [get it here](https://github.com/TorecLuik/docker-example-omero-grid-amc/tree/processors).

```bash
git clone -b processors https://github.com/TorecLuik/docker-example-omero-grid-amc.git
```

Change the `worker-gpu/slurm-config.ini` file to point to `gcslurm` profile (or rename your SSH profile to `slurm`)
```ini
[SSH]
# -------------------------------------
# SSH settings
# -------------------------------------
# The alias for the SLURM SSH connection
host=gcslurm
```

This way, it will use the right SSH setting to connect with our Google Cloud Slurm.


Let's (build it and) fire it up:

```bash
docker-compose up -d --build
```

======= Omero web =======

Once they are running, you should be able to access web at `localhost:4080`. Login with user `root` / pw `omero`. 

Import some example data with Omero Insight (connect with `localhost`).

======= Connect to Slurm =======

This container's processor node (`worker-5`) has already installed our `omero-slurm-client` library. 

======= Add ssh config to Omero Processor =======

Ok, so `localhost` works fine from your machine, but we need the Omero processing server `worker-5` to be able to do it too, like [we did before](#2c-add-ssh-config-for-simple-login).

By some smart tricks, we have mounted our `~/.ssh` folder to the worker container, so it knows and can use our SSH settings and config.

Ok, so now we can connect from within the worker-5 to our Slurm cluster. We can try it out:
```powershell
...\docker-example-omero-grid> docker-compose exec omeroworker-5 /bin/bash
bash-4.2$ ssh slurm
Last login: Wed Aug  9 13:08:54 2023 from 172.21.0.1
[slurm@slurmctld ~]$ squeue
             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
[slurm@slurmctld ~]$ exit
logout
Connection to host.docker.internal closed.
bash-4.2$ exit
exit
```

======= slurm-config.ini =======
 
Let us setup the library's config file [slurm-config.ini](../slurm-config.ini) correctly.

Now, the `omero-slurm-client` library by default expects the `Slurm` ssh connection to be called `slurm`, but you can adjust it to whatever you named your ssh _Host_ in config. 

In this Docker setup, the config file is located at the `worker-gpu` folder and in the Dockerfile it is copied to `/etc/`, where the library will pick it up.

Let's use these values:

```ini
[SSH]
# -------------------------------------
# SSH settings
# -------------------------------------
# The alias for the SLURM SSH connection
host=slurm
# Set the rest of your SSH configuration in your SSH config under this host name/alias
# Or in e.g. /etc/fabric.yml (see Fabric's documentation for details on config loading)

[SLURM]
# -------------------------------------
# Slurm settings
# -------------------------------------
# General settings for where to find things on the Slurm cluster.
# -------------------------------------
# PATHS
# -------------------------------------
# The path on SLURM entrypoint for storing datafiles
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_data_path=/data/my-scratch/data
# The path on SLURM entrypoint for storing container image files
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_images_path=/data/my-scratch/singularity_images/workflows
# The path on SLURM entrypoint for storing the slurm job scripts
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_script_path=/data/my-scratch/slurm-scripts
```

We have put all the storage paths on `/data/my-scratch/` and named the SSH Host connection `slurm`.

The other values we can keep as [default](../slurm-config.ini), except we don't have a GPU, so let's turn that off for CellPose:

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
# For more examples of such parameters, google SBATCH parameters.
# If you don't want to override, comment out / delete the line.
# Run CellPose Slurm with 10 GB GPU
# cellpose_job_gres=gpu:1g.10gb:1
# Run CellPose Slurm with 15 GB CPU memory
cellpose_job_mem=15GB
```

The `gres` will request a 10GB GPU on the Slurm cluster, but we only set up CPU docker slurm.

We will also comment out some of the other algorithms, so we have to download less containers to our Slurm cluster and speed up the tutorial.

This brings us to the following configuration file:


```ini
[SSH]
# -------------------------------------
# SSH settings
# -------------------------------------
# The alias for the SLURM SSH connection
host=slurm
# Set the rest of your SSH configuration in your SSH config under this host name/alias
# Or in e.g. /etc/fabric.yml (see Fabric's documentation for details on config loading)

[SLURM]
# -------------------------------------
# Slurm settings
# -------------------------------------
# General settings for where to find things on the Slurm cluster.
# -------------------------------------
# PATHS
# -------------------------------------
# The path on SLURM entrypoint for storing datafiles
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_data_path=/data/my-scratch/data
# The path on SLURM entrypoint for storing container image files
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_images_path=/data/my-scratch/singularity_images/workflows
# The path on SLURM entrypoint for storing the slurm job scripts
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_script_path=/data/my-scratch/slurm-scripts
# -------------------------------------
# REPOSITORIES
# -------------------------------------
# A (github) repository to pull the slurm scripts from.
#
# Note: 
# If you provide no repository, we will generate scripts instead!
# Based on the job_template and the descriptor.json
#
# Example:
#slurm_script_repo=https://github.com/TorecLuik/slurm-scripts
slurm_script_repo=
# -------------------------------------
# Processing settings
# -------------------------------------
# General/default settings for processing jobs.
# Note: NOT YET IMPLEMENTED
# Note: If you need to change it for a specific case only,
# you should change the job script instead, either in Omero or Slurm 


[MODELS]
# -------------------------------------
# Model settings
# -------------------------------------
# Settings for models/singularity images that we want to run on Slurm
#
# NOTE: keys have to be unique, and require a <key>_repo and <key>_image value as well.
#
# NOTE 2: Versions for the repo are highly encouraged! 
# Latest/master can change and cause issues with reproducability!
# We pickup the container version based on the version of the repository.
# For generic master branch, we pick up generic latest container.
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
# For more examples of such parameters, google SBATCH parameters.
# If you don't want to override, comment out / delete the line.
# Run CellPose Slurm with 10 GB GPU
# cellpose_job_gres=gpu:1g.10gb:1
# Run CellPose Slurm with 15 GB CPU memory
cellpose_job_mem=15GB
# -------------------------------------
# # STARDIST SEGMENTATION
# # -------------------------------------
# # The path to store the container on the slurm_images_path
# stardist=stardist
# # The (e.g. github) repository with the descriptor.json file
# stardist_repo=https://github.com/Neubias-WG5/W_NucleiSegmentation-Stardist/tree/v1.3.2
# # The jobscript in the 'slurm_script_repo'
# stardist_job=jobs/stardist.sh
# -------------------------------------
# CELLPROFILER SEGMENTATION
# # -------------------------------------
# # The path to store the container on the slurm_images_path
# cellprofiler=cellprofiler
# # The (e.g. github) repository with the descriptor.json file
# cellprofiler_repo=https://github.com/Neubias-WG5/W_NucleiSegmentation-CellProfiler/tree/v1.6.4
# # The jobscript in the 'slurm_script_repo'
# cellprofiler_job=jobs/cellprofiler.sh
# -------------------------------------
# DEEPCELL SEGMENTATION
# # -------------------------------------
# # The path to store the container on the slurm_images_path
# deepcell=deepcell
# # The (e.g. github) repository with the descriptor.json file
# deepcell_repo=https://github.com/Neubias-WG5/W_NucleiSegmentation-DeepCell/tree/v.1.4.3
# # The jobscript in the 'slurm_script_repo'
# deepcell_job=jobs/deepcell.sh
# -------------------------------------
# IMAGEJ SEGMENTATION
# # -------------------------------------
# # The path to store the container on the slurm_images_path
# imagej=imagej
# # The (e.g. github) repository with the descriptor.json file
# imagej_repo=https://github.com/Neubias-WG5/W_NucleiSegmentation-ImageJ/tree/v1.12.10
# # The jobscript in the 'slurm_script_repo'
# imagej_job=jobs/imagej.sh
# # -------------------------------------
# # CELLPROFILER SPOT COUNTING
# # -------------------------------------
# The path to store the container on the slurm_images_path
cellprofiler_spot=cellprofiler_spot
# The (e.g. github) repository with the descriptor.json file
cellprofiler_spot_repo=https://github.com/TorecLuik/W_SpotCounting-CellProfiler/tree/v1.0.1
# The jobscript in the 'slurm_script_repo'
cellprofiler_spot_job=jobs/cellprofiler_spot.sh
# # -------------------------------------
# CELLEXPANSION SPOT COUNTING
# -------------------------------------
# The path to store the container on the slurm_images_path
cellexpansion=cellexpansion
# The (e.g. github) repository with the descriptor.json file
cellexpansion_repo=https://github.com/TorecLuik/W_CellExpansion/tree/v1.0.1
# The jobscript in the 'slurm_script_repo'
cellexpansion_job=jobs/cellexpansion.sh
```

======= Init environment =======

Now we go to Omero web and run the `slurm/init_environment` script to apply this config and setup our Slurm. We will use the default location, no need to fill in anything, just run the script.

![Slurm Init Busy](./images/webclient_init_env.PNG)

![Slurm Init Done](./images/webclient_init_env_done.PNG)

</details>

Note, this will take a while, since it is downloading workflow docker images and building (singularity) containers from them. 

Congratulations! We have setup workflows CellPose `v1.2.7`, Cellprofiler Spot `v1.0.1` and CellExpansion `v1.0.1`. And there are no data files yet.

Let's go run some segmentation workflow then!

## 5. Workflows!

### TL;DR:
1. In web, select your images and run script `slurm/SLURM Run Workflow`
    - Tick off `E-mail` box (not implemented in this Slurm docker setup)
    - For importing results, change `3a) Import into NEW Dataset` to `CellPose_Masks`
    - For importing results, change `3b) Rename the imported images` to `{original_file}_cpmask.{ext}`
    - Select `cellpose`, but tick off `use_gpu` off (sadly not implemented in this docker setup)
    - Click `Run Script`
2. Check activity window (or get a coffee), it should take a few minutes (about 3m:30s for 4 256x256 images for me) and then say (a.o.): `COMPLETE`
    - Or it `FAILED`, in which case you should check all the details anyway and get your hands dirty with debugging! Or try less and smaller images.
3. Refresh your Explore window, there should be a new dataset `CellPose_Masks` with a mask for every input image. 

<details>
  <summary>Details</summary>

So, I hope you added some data already; if not, import some images now.

Let's run `slurm/SLURM Run Workflow`:

![Slurm Run Workflow](./images/webclient_run_workflow.PNG?raw=true)

You can see that this script recognized that we downloaded 3 workflows, and what their parameters are. For more information on this magic, follow the other tutorials.

Let's select `cellpose` and click `use gpu` off (sadly). Tune the other parameters as you like for your images. Also, for output let's select `Import into NEW Dataset` by filling in a dataset name: cellpose_images. Click `Run Script`.

![Slurm Run Cellpose](./images/webclient_run_cellpose.PNG?raw=true)

Result: Job 1 is FAILED.
Turns out, our Slurm doesn't have the compute nodes to execute this operation.

======= Improve Slurm =======

Update the `slurm.conf` file in the git repository.

```ini
# COMPUTE NODES
NodeName=c[1-2] RealMemory=5120 CPUs=8 State=UNKNOWN
```

Here, 5GB and 8 CPU each should do the trick!

Rebuild the containers. Note that the config is on a shared volume, so we have to destroy that volume too (it took some headbashing to find this out):
```powershell
docker-compose down --volumes 
```
```powershell
docker-compose up --build
```

</details>

That should take you through connecting Omero with a local Slurm setup.

### Batching

Try `slurm/SLURM Run Workflow Batched` to see if there is any speedup by splitting your images over multiple jobs/batches. 

We have installed 2 nodes in this Slurm cluster, so you could make 2 batches of half the images and get your results quicker. However we are also limited to compute 2 jobs in parallel, so smaller (than half) batches will just wait in the queue (with some overhead) and probably take longer.

<details>
Note that there is always overhead cost, so the speedup will not be linear. However, the more time is in compute vs overhead, the more gains you should get by splitting over multiple jobs / nodes / CPUs.

Let's check on the Slurm node:
```bash
$ sacct --starttime "2023-06-13T17:00:00" --format Jobid,State,start,end,JobName%-18,Elapsed -n -X --endtime "now"
``` 

In my latest example, it was 1 minute (30%) faster to have 2 batches/jobs (`32` & `33`) vs 1 job (`31`):
```yaml
31            COMPLETED 2023-08-23T08:41:28 2023-08-23T08:45:02 omero-job-cellpose   00:03:34

32            COMPLETED 2023-08-23T09:22:00 2023-08-23T09:24:27 omero-job-cellpose   00:02:27
33            COMPLETED 2023-08-23T09:22:03 2023-08-23T09:24:40 omero-job-cellpose   00:02:37
```


</details>
