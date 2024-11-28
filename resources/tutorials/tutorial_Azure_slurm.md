# Microsoft Azure Slurm tutorial

## Introduction

This library is meant to be used with some external HPC cluster using Slurm, to offload your (OMERO) compute to servers suited for it.

However, if you don't have ready access (yet) to such a cluster, you might want to spin some test environment up in the Cloud and connect your (local) OMERO to it. 
This is what we will cover in this tutorial, specifically **Microsoft Azure**.

## 0. Requirements

To follow this tutorial, you need:
- OMERO Insight
- An Azure account (and credits)

I try to provide a tl;dr when I can, otherwise I go step by step.


## 1. Setup Microsoft Azure for Slurm

### TL;DR:
1. Make a new Azure account if you don't have one. Hopefully you get/have some free credits.
2. Create an new App "BIOMERO" via "App registrations"
    - Copy Application ID
    - Copy Application Secret
3. Assign roles to App "BIOMERO":
    - "Azure Container Storage Operator" role on the Subscription (or probably on the Resource Group works too)
    - "Virtual Machine Contributor" role on the Resource Group ("biomero-public")
    - "Network Contributor" role on the Resource Group ("biomero-public")
4. Create storageaccount "biomerostorage" in the "biomero-public" Resource Group
5. Mainly: Follow this video [tutorial](https://www.youtube.com/watch?v=qIm6PAFVsmU) from Microsoft Azure
    - However, note that I actually have trouble with their specific version of Slurm in CycleCloud and the default version works fine. Checkout the `details` below for more details on this part.
6. Probably use something cheaper than 4x the expensive `Standard_ND96amsr_A100_v4` instances, unless you are really rich!
    - Note: Use `Ds` not `Das` or `Des` VM types, if you run into `security type <null>` errors in deployment.
7. We need a Slurm accounting database for BIOMERO! See `1 - Addendum` chapter below for setting one up, if you don't have a database.
7. Add a public key to your Azure CycleCloud profile. Probably use the `hpc-slurm-cluster_key` that you can find in your Resource Group.
8. Now you should be able to login to the Slurm scheduler with something like `ssh -i C:\<path-to-my>\hpc-slurm-cluster_key.pem azureadmin@<scheduler-vm-public-ip>`
9. Change the cloud-init to install Singularity and 7zip on your nodes.

<details>
  <summary>Details</summary>

So, we follow this [tutorial](https://www.youtube.com/watch?v=qIm6PAFVsmU) and end up with a `hpc-slurm-cluster` (that's what I named the VM) VM on Microsoft Azure. It also downloaded the SSH private key for us (`hpc-slurm-cluster_key.pem`).

---
### Suggested alternative: use a basic Slurm cluster
CycleCloud already comes with a basic Slurm setup, that is more up-to-date than this specific GPU powered version.
Especially if you will not use GPU anyway (because $$).

So, given you followed the movie to get a CycleCloud VM up and running, let's setup a basic Slurm cluster instead.

Let's start that up:
- Click `+` / `Add` for a new cluster and select `Slurm` (instead of `cc-slurm-ngc`)
- We provide a new name `biomero-cluster-basic`
- We change all the VM types:
    - Scheduler: `Standard_DC4s_v3`
    - HPC, HTC and Dyn: `Standard_DC2s_v3`
    - Login node we will not use so doesn't matter (`Num Login Nodes` stays `0`)
- We change the scaling amount to only 4 cores each (instead of 100), and MaxVMs to 2.
- Change the network to the default biomero network
- Next, keep all the `Network Attached Storage` settings
- Next, `Advanced Settings`
  - Here, we need to do 2 major things:
  - First, add the Slurm accounting database. See `1 - Addendum` chapter below for setting that up.
  - Second, select appropriate VM images that will work for our software (mainly `singularity` for containers): `Ubuntu 22.04 LTS` worked for us.
- Next, keep all the security settings
- Finally, let's [change Cloud init](https://learn.microsoft.com/en-us/answers/questions/404297/installing-singularity-on-a-slurm-cyclecloud-clust) for _all_ nodes, to install singularity and 7zip:


```
#cloud-config  
package_upgrade: true
packages:  
  - htop
  - wget
  - p7zip-full
  - software-properties-common

runcmd:  
  - 'sudo add-apt-repository -y ppa:apptainer/ppa'
  - 'sudo apt update'
  - 'sudo apt install -y apptainer'
```

Apptainer is singularity and will provide the singularity (alias) command.



</details>

## 1 - Addendum - Setting up Slurm Accounting DB
0. Expected price: 16.16 euro / month
1. We follow [this recent blog](https://techcommunity.microsoft.com/t5/azure-high-performance-computing/setting-up-slurm-job-accounting-with-azure-cyclecloud-and-azure/ba-p/4083685)
    - Note that `Western Europe` has deployment issues on Azure for these DB. See `Details` for more details.


<details>
  <summary>Details</summary>

#### A. Create extra subnet on your virtual network
- Go to your `hpc-slurm-cluster-vnet`
- Go to `Subnets` Settings 
- Create a new subnet with `+ Subnet`, named `mysql` (and default settings)


#### B. Azure database
We create a [MySQL Flexible Server](https://portal.azure.com/#create/Microsoft.MySQLFlexibleServer) 

- Server name `slurm-accounting-database` (or whatever is available)
- Region `Western Europe` (same as your server/VNET).
  - See Notes below about issues (and solutions) in `Western Europe` at the time of writing.
- MySQL version `8.0`
- Workload type `For development` (unless you are being serious)
- Authentication method `MySQL authentication only`
- User credentials that you like, I used `omero` user.
- Next: Networking
  - Connectivity method: `Private access (VNet Integration)`
  - Virtual network: select your existing `hpc-slurm-cluster-vnet` net
  - Subnet: select your new subnet `hpc-slurm-cluster-vnet/mysql`
  - Private DNS, let it create or use existing one.
- Next: deploy it!

Next, let's change some Server parameters according to the blog.
I think this is optional though.
1. Go to your `slurm-accounting-database` in the Azure portal
2. Open on left-hand side `Server parameters`
3. Click on `All`
4. Filter on `innodb_lock_wait_timeout`
5. Change value to `900`
6. Save changes

Note! Availability issue in `Western Europe` in march 2024:

We had an issue deploying the database in `Western Europe`, apparently it is full there. So we deployed the database in `UK South` instead. If you have different regions, you need to connect the VNETs of both regions though, through something called `peering`! 

For this to work, make sure the IPs of the subnets do not overlap, see `A` where we made an extra subnet with different IP. 
  - We made some extra `biomero-public-vnet` with a `mysql` subnet on the `10.1.0.0/24` range. Make sure it is also `Delegated to` `Microsoft.DBforMySQL/flexibleServers`.
  - Remove the `default` subnet
  - Remove the `10.0.0.0/24` address space (as we will connect the other vnet here)
  - Then go to the `biomero-public-vnet`, `Peerings` and `+ Add` a new peering.
  - First named `hpc`, default settings
  - Remote named `acct`, connecting to the `hpc-slurm-cluster-vnet`.

#### C. Slurm Accounting settings

Ok, now back in CycleCloud, we will set Slurm Accounting:
1. Edit cluster
2. Advanced Settings
3. Check the `Configure Slurm job accounting` box
  - `Slurm DBD URL` will be 
your chosen Server name (check the Azure portal). For me it was `slurm-accounting-database.mysql.database.azure.com`.
  - `Slurm DBD User` and `... Password` are what entered in deployment for the DB.
  - SSL Certificate URL is `https://dl.cacerts.digicert.com/DigiCertGlobalRootCA.crt.pem`
4. (Re)start your Slurm cluster.
5. Test out if the `sacct` command works! 

</details>



## 2. Test Slurm

1. SSH into the login node: `ssh gcslurm`
3. Start some filler jobs: `sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname" &&  sbatch --wrap="sleep 5 && hostname"`
4. Check the progress: `squeue` (perhaps also check Azure CycleCloud to see your HPC VMs spinning up, takes a few min)
5. Check some output when its done, e.g. job 1: `cat slurm-1.out`

## 3. Test Singularity on Slurm

For example, run:

    sbatch -n 1 --wrap "hostname > lolcow.log && singularity run docker://godlovedc/lolcow >> lolcow.log"

This should say "Submitted batch job 1"
Then let's tail the logfile:

    tail -f lolcow.log

First we see the slurm node that is computing, and later we will see the funny cow.

```bash
[slurm@slurmctld data]$ tail -f lolcow.log
c1
 _______________________________________
/ Must I hold a candle to my shames?    \
|                                       |
| -- William Shakespeare, "The Merchant |
\ of Venice"                            /
 ---------------------------------------
        \   ^__^
         \  (oo)\_______
            (__)\       )\/\
                ||----w |
                ||     ||
```

Exit logs with `CTRL+C`, and the server with `exit`, and enjoy your Azure Slurm cluster.



## 5. Setting up (BI)OMERO in Azure too (Optional)

We will install (BI)OMERO on the CycleCloud VM that we have running anyway.
Alternatively, you connect your local (BI)OMERO to this cluster now.

1. SSH into your CycleCloud VM, `hpc-slurm-cluster` as `azureuser`

` ssh -i C:\<path-to>\hpc-slurm-cluster_key.pem azureuser@<public-ip>`

2. [Install a container runner like Docker](https://www.liquidweb.com/kb/install-docker-on-linux-almalinux/)

3. Ensure it works so you can look at the lolcow again `docker run godlovedc/lolcow`

Ok, good enough. 

Now let's pull an easy BIOMERO setup from [NL-BIOMERO](https://github.com/Cellular-Imaging-Amsterdam-UMC/NL-BIOMERO.git) onto our VM:

1. `git clone https://github.com/Cellular-Imaging-Amsterdam-UMC/NL-BIOMERO.git`

2. Let's test it: `docker compose up -d --build`

3. Now we need to open the OMERO web port to view it `4080`.
  - First, go to Azure portal and click on your VM `hpc-slurm-cluster`
  - Second, go to Networking > Network settings
  - Third, `Create port rules` > `Inbound port rule`
    - Destination port ranges `4080`, Protocol `TCP`, Name `OMEROWEB`. Add it. And wait a bit for it to take effect.

4. Test it! Open your web browser at `<public-ip>:4080` and login with `root`/`omero`

Good start!

Now let's connect BIOMERO to our HPC Slurm cluster:

1. Copy the SSH private key `hpc-slurm-cluster_key.pem` (from chapter 1) to the (CycleCloud/OMERO) server:

`scp -c C:\<path>\hpc-slurm-cluster_key.pem C:\<path>\hpc-slurm-cluster_key.pem azureuser@<public-ip>:~`

2. Copy your key on the server into `~/.ssh` and change permissions, log in:

- `cp hpc-slurm-cluster_key.pem .ssh`
- `sudo chmod 700 .ssh/hpc-slurm-cluster_key.pem`
- `ssh -i .ssh/hpc-slurm-cluster_key.pem azureadmin@<scheduler-ip>`
- Great, exit back to the CycleCloud server.

The IP of the scheduler (this changes whenever you create a new cluster!) is shown in the Azure CycleCloud screen, when you click on the active scheduler node.

3. Create a config to setup an alias for the SSH

  - `vi ~/.ssh/config`
  - press `i` to *insert* text
  - copy paste / fill in the config:

```
Host localslurm
        HostName <scheduler-ip>
        User azureadmin
        Port 22
        IdentityFile ~/.ssh/hpc-slurm-cluster_key.pem
        StrictHostKeyChecking no
```
Fill in the actual ip, this is just a placeholder!
  - Save with escape followed by `:wq`
  - chmod the config to 700 too: `sudo chmod 700 .ssh/config`
  - Ready! `ssh localslurm` (or whatever you called the alias)

4. Let's edit the BIOMERO configuration `slurm-config.ini`, located in the biomeroworker node

  - `vi ~/NL-BIOMERO/biomeroworker/slurm-config.ini`
  - Change the `host` if you did not use the `localslurm` alias in the config above.
  - Change ALL the `[SLURM]` paths to match our new slurm setup:

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
slurm_data_path=data
# The path on SLURM entrypoint for storing container image files
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_images_path=singularity_images/workflows
# The path on SLURM entrypoint for storing the slurm job scripts
#
# Note: 
# This example is relative to the Slurm user's home dir
slurm_script_path=slurm-scripts
```

  - Save the file again with escape + `:wq`

5. Now we need to do some Linux shenanigans to mount ssh properly into the container

  - First, create a empty .pub file that we are missing: `touch ~/.ssh/empty.pub`
  - Second, chmod the .ssh folder and its contents fully open so Docker can access it: `chmod -R 777 ~/.ssh`
  - Note, if you later want to SSH from commandline again (instead of letting BIOMERO do it), just change the rights back to 700 (`chmod -R 700 ~/.ssh`). This is just a Linux container building temporary permission thing.

6. Now we will (re)start / (re)build the BIOMERO servers again

  - `cd NL-BIOMERO`
  - `docker compose down`
  - `docker compose up -d --build`
  - Now `docker logs -f nl-biomero-biomeroworker-1` should show some good logs leading to: `Starting node biomeroworker`.


## 6. Showtime!

1. Go to your OMERO web at `http://<your-VM-ip>:4080/` (`root`/`omero`)
2. Let's initialize BIOMERO: `Run Script` > `biomero` > `init` > `SLURM Init environment...`; run that script
3. While we're waiting for that to complete, let's checkout the basic connection: `Run Script` > `biomero` > `Example Minimal Slurm Script...`;
  - Uncheck the `Run Python` box, as we didn't install that
  - Check the `Check SLURM status` box
  - Check the `Check Queue` box
  - Run the script, you should get something like this, an empty queue:

```sh
JOBID PARTITION NAME USER ST TIME NODES NODELIST(REASON)
```
  
  - You can try some other ones, e.g. check the `Check Cluster` box instead:

```sh
PARTITION AVAIL TIMELIMIT NODES STATE NODELIST 
dynamic up infinite 0 n/a 
hpc* up infinite 2 idle~ biomero-cluster-basic-hpc-[1-2] 
htc up infinite 2 idle~ biomero-cluster-basic-htc-[1-2]
```
  - Note if you click the `i` button next to the output, you can see the output printed in a lot more detail and better formatting. Especially if you ran multiple commands at the same time.

4. At some point, the init script will be done, or you get a `Ice.ConnectionLostException` (which means it took too long).

  - Let's see what BIOMERO created! Run  `Run Script` > `biomero` > `Example Minimal Slurm Script...`;
  - Uncheck the `Run Python` box
  - Check the `Run Other Commmand` box
  - Change the Linux Command to `ls -la **/*` (we want to check all subfolders too).
  - Run it. Press the `i` button for proper formatting and scroll down to see what we made

```sh
=== stdout ===
-rw-r--r-- 1 azureadmin azureadmin 1336 Mar 21 17:44 slurm-scripts/convert_job_array.sh

my-scratch/singularity_images:
total 0
drwxrwxr-x 3 azureadmin azureadmin  24 Mar 21 17:31 .
drwxrwxr-x 3 azureadmin azureadmin  32 Mar 21 17:31 ..
drwxrwxr-x 2 azureadmin azureadmin 117 Mar 21 17:45 converters

singularity_images/workflows:
total 16
drwxrwxr-x 6 azureadmin azureadmin   126 Mar 21 17:31 .
drwxrwxr-x 3 azureadmin azureadmin    23 Mar 21 17:31 ..
drwxrwxr-x 2 azureadmin azureadmin    40 Mar 21 17:42 cellexpansion
drwxrwxr-x 2 azureadmin azureadmin    54 Mar 21 17:36 cellpose
drwxrwxr-x 2 azureadmin azureadmin    52 Mar 21 17:40 cellprofiler_spot
-rw-rw-r-- 1 azureadmin azureadmin   695 Mar 21 17:45 pull_images.sh
-rw-rw-r-- 1 azureadmin azureadmin 10802 Mar 21 17:45 sing.log
drwxrwxr-x 2 azureadmin azureadmin    43 Mar 21 17:44 spotcounting

slurm-scripts/jobs:
total 16
drwxrwxr-x 2 azureadmin azureadmin  100 Mar 21 17:31 .
drwxrwxr-x 3 azureadmin azureadmin   46 Mar 21 17:31 ..
-rw-rw-r-- 1 azureadmin azureadmin 3358 Mar 21 17:44 cellexpansion.sh
-rw-rw-r-- 1 azureadmin azureadmin 3406 Mar 21 17:44 cellpose.sh
-rw-rw-r-- 1 azureadmin azureadmin 3184 Mar 21 17:44 cellprofiler_spot.sh
-rw-rw-r-- 1 azureadmin azureadmin 3500 Mar 21 17:44 spotcounting.sh
```

 - Or better yet, run this linux command for full info on _all_ (non-hidden) subdirectories: `find . -type d -not -path '*/.*' -exec ls -la {} +`. This should show that we downloaded some of the workflows to our Slurm cluster already:

 ```sh
 ./singularity_images/workflows:
total 16
drwxrwxr-x 6 azureadmin azureadmin   126 Mar 21 17:31 .
drwxrwxr-x 3 azureadmin azureadmin    23 Mar 21 17:31 ..
drwxrwxr-x 2 azureadmin azureadmin    40 Mar 21 17:42 cellexpansion
drwxrwxr-x 2 azureadmin azureadmin    54 Mar 21 17:36 cellpose
drwxrwxr-x 2 azureadmin azureadmin    52 Mar 21 17:40 cellprofiler_spot
-rw-rw-r-- 1 azureadmin azureadmin   695 Mar 21 17:45 pull_images.sh
-rw-rw-r-- 1 azureadmin azureadmin 10802 Mar 21 17:45 sing.log
drwxrwxr-x 2 azureadmin azureadmin    43 Mar 21 17:44 spotcounting

./singularity_images/workflows/cellexpansion:
total 982536
drwxrwxr-x 2 azureadmin azureadmin         40 Mar 21 17:42 .
drwxrwxr-x 6 azureadmin azureadmin        126 Mar 21 17:31 ..
-rwxr-xr-x 1 azureadmin azureadmin 1006116864 Mar 21 17:42 w_cellexpansion_v2.0.1.sif

./singularity_images/workflows/cellpose:
total 4672820
drwxrwxr-x 2 azureadmin azureadmin         54 Mar 21 17:36 .
drwxrwxr-x 6 azureadmin azureadmin        126 Mar 21 17:31 ..
-rwxr-xr-x 1 azureadmin azureadmin 4784967680 Mar 21 17:36 t_nucleisegmentation-cellpose_v1.2.9.sif

./singularity_images/workflows/cellprofiler_spot:
total 2215916
drwxrwxr-x 2 azureadmin azureadmin         52 Mar 21 17:40 .
drwxrwxr-x 6 azureadmin azureadmin        126 Mar 21 17:31 ..
-rwxr-xr-x 1 azureadmin azureadmin 2269097984 Mar 21 17:40 w_spotcounting-cellprofiler_v1.0.1.sif

./singularity_images/workflows/spotcounting:
total 982720
drwxrwxr-x 2 azureadmin azureadmin         43 Mar 21 17:44 .
drwxrwxr-x 6 azureadmin azureadmin        126 Mar 21 17:31 ..
-rwxr-xr-x 1 azureadmin azureadmin 1006305280 Mar 21 17:44 w_countmaskoverlap_v1.0.1.sif

./slurm-scripts:
total 8
drwxrwxr-x  3 azureadmin azureadmin   46 Mar 21 17:31 .
drwxr-xr-x 12 azureadmin azureadmin 4096 Mar 21 17:31 ..
-rw-r--r--  1 azureadmin azureadmin 1336 Mar 21 17:44 convert_job_array.sh
drwxrwxr-x  2 azureadmin azureadmin  100 Mar 21 17:31 jobs

./slurm-scripts/jobs:
total 16
drwxrwxr-x 2 azureadmin azureadmin  100 Mar 21 17:31 .
drwxrwxr-x 3 azureadmin azureadmin   46 Mar 21 17:31 ..
-rw-rw-r-- 1 azureadmin azureadmin 3358 Mar 21 17:44 cellexpansion.sh
-rw-rw-r-- 1 azureadmin azureadmin 3406 Mar 21 17:44 cellpose.sh
-rw-rw-r-- 1 azureadmin azureadmin 3184 Mar 21 17:44 cellprofiler_spot.sh
-rw-rw-r-- 1 azureadmin azureadmin 3500 Mar 21 17:44 spotcounting.sh
 ```

5. Ok, let's get to some data! Upload a file with (a local installation of) omero insight.
  - First, open up the OMERO port `4064` in Azure on your `hpc-slurm-cluster`, just like we did with port `4080`: Add inbound security rule, destination `4064`, Protocol `TCP`, Name `OMEROINSIGHT`.
  - Change the server to `<cyclecloud-vm-ip>:4064`
  - Login `root`/`omero`
  - Upload some Nuclei fluorescense images. For example, I uploaded the raw images from [S-BSST265](https://www.ebi.ac.uk/biostudies/bioimages/studies/S-BSST265) into a Project `TestProject` and Dataset `S-BSST265`. Add to Queue, and import!

6. IMPORTANT! Our default [job script](https://github.com/NL-BioImaging/biomero/blob/main/resources/job_template.sh#L12) assumes 4 CPUs, but we have nodes with only 2 cores. So we have to lower this amount for the job script. Otherwise we get this error:

```
sbatch: error: CPU count per node can not be satisfied
sbatch: error: Batch job submission failed: Requested node configuration is not available
```

We will do this ad-hoc, by changing the configuration for CellPose in the `slurm-config.ini` in our installation:

  - First, edit the config on the main VM with `vi biomeroworker/slurm-config.ini`
  - Add this line to your workflows `<wf>_job_cpus-per-task=2`, e.g. `cellpose_job_cpus-per-task=2` 
  - save file (`:wq`)
  - Don't forget to open your .ssh to the container `chmod -R 777 ~/.ssh` (and close it later)
  - Restart the biomero container(s) (`docker compose down` & `docker compose up -d --build`, perhaps specifically for `biomeroworker`).
  - Check logs to see if biomero started up properly `docker logs -f nl-biomero-biomeroworker-1` 


6. Next, time to segment! Time to spin up those SLURM compute nodes:
  - First, select your newly imported dataset, then `Run Script` > `biomero` > `workflows` > `SLURM Run Workflow...`
  - At `Select how to import your results (one or more)`, we will upload the masks back into a new dataset, so:
    - Change `3a) Import into NEW Dataset:` into `CellPoseMasks`
    - Change `3c) Rename the imported images:` into `{original_file}_Mask_C1.{ext}` (these are placeholder values)
  - Next, check the `cellpose` box and
    - Change `nuc channel` to `1`
    - Uncheck the `use gpu` box (unless you paid for sweet GPU nodes from Azure)
  - Run Script! 
    - We are running the `cellpose` workflow on channel `1` (with otherwise default parameters) of all the images of the dataset `S-BSST265` and import the output mask images back into OMERO as dataset `CellPoseMasks`.
  - Now, this will take a while again because we are cheap and do not have a GPU node at the ready. 
    - Instead, Azure will `on-demand` create our compute node (to save us money when we are not using it), which only has a few CPUs as well! 
    - So this is not a test of speed (unless you setup a nice Slurm cluster with always-available GPU nodes), but of the BIOMERO automation process.


# Extra thoughts

- Perhaps also make the Cluster have a static IP, instead of changing whenever you terminate it: https://learn.microsoft.com/en-us/azure/cyclecloud/how-to/network-security?view=cyclecloud-8







