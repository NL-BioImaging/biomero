# BIOMERO - BioImage analysis in OMERO
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![DOI](https://zenodo.org/badge/638954891.svg)](https://zenodo.org/badge/latestdoi/638954891) [![PyPI - Version](https://img.shields.io/pypi/v/biomero)](https://pypi.org/project/biomero/) [![PyPI - Python Versions](https://img.shields.io/pypi/pyversions/biomero)](https://pypi.org/project/biomero/) ![Slurm](https://img.shields.io/badge/Slurm-21.08.6-blue.svg) ![OMERO](https://img.shields.io/badge/OMERO-5.6.8-blue.svg) [![fair-software.eu](https://img.shields.io/badge/fair--software.eu-%E2%97%8F%20%20%E2%97%8F%20%20%E2%97%8B%20%20%E2%97%8F%20%20%E2%97%8F-yellow)](https://fair-software.eu) [![OpenSSF Best Practices](https://bestpractices.coreinfrastructure.org/projects/7530/badge)](https://bestpractices.coreinfrastructure.org/projects/7530) [![Sphinx build](https://github.com/NL-BioImaging/biomero/actions/workflows/sphinx.yml/badge.svg?branch=main)](https://github.com/NL-BioImaging/biomero/actions/workflows/sphinx.yml) [![pages-build-deployment](https://github.com/NL-BioImaging/biomero/actions/workflows/pages/pages-build-deployment/badge.svg)](https://github.com/NL-BioImaging/biomero/actions/workflows/pages/pages-build-deployment) [![python-package build](https://github.com/NL-BioImaging/biomero/actions/workflows/python-package.yml/badge.svg)](https://github.com/NL-BioImaging/biomero/actions/workflows/python-package.yml) [![python-publish build](https://github.com/NL-BioImaging/biomero/actions/workflows/python-publish.yml/badge.svg?branch=main)](https://github.com/NL-BioImaging/biomero/actions/workflows/python-publish.yml)

The **BIOMERO** framework, for **B**io**I**mage analysis in **OMERO**, allows you to run (FAIR) bioimage analysis workflows directly from OMERO on a high-performance compute (HPC) cluster, remotely through SSH.

The BIOMERO framework consists of this Python library `biomero`, together with the [BIOMERO scripts](https://github.com/NL-BioImaging/biomero-scripts) that can be run directly from the OMERO web interface.

The package includes the `SlurmClient` class, which provides **SSH-based connectivity** and interaction with a [Slurm](https://slurm.schedmd.com/quickstart.html) (high-performance compute) cluster. The package enables users to submit jobs, monitor job status, retrieve job output, and perform other Slurm-related tasks. Additionally, the package offers functionality for configuring and managing paths to Slurm data and Singularity images (think Docker containers...), as well as specific FAIR image analysis workflows and their associated repositories. 

Overall, the `biomero` package simplifies the integration of HPC functionality within the OMERO platform for admins and provides an efficient and end-user-friendly interface towards both the HPC and FAIR workflows.

# Overview

In the figure below we show our **BIOMERO** framework, for **B**io**I**mage analysis in **OMERO**. 

BIOMERO consists of this Python library (`biomero`) and the integrations within OMERO, currently through our [BIOMERO scripts](https://github.com/NL-BioImaging/biomero-scripts).

![OMERO-Figure1_Overview_v5](https://github.com/NL-BioImaging/biomero/assets/68958516/ff437ed2-d4b7-48b4-a7e3-12f1dbf00981)



# Quickstart



For a quick overview of what this library can do for you, we can install an example setup locally with Docker:

1. Setup a local OMERO w/ this library: 
    - Follow Quickstart of https://github.com/Cellular-Imaging-Amsterdam-UMC/NL-BIOMERO
2. Setup a local Slurm w/ SSH access: 
    - Follow Quickstart of https://github.com/TorecLuik/slurm-docker-cluster
3. Upload some data with OMERO.insight to `localhost` server (... we are working on a web importer ... TBC)
4. Try out some scripts from https://github.com/NL-BioImaging/biomero-scripts (already installed in step 1!):
    1. Run script `slurm/init/SLURM Init environment...`
    2. Get a coffee or something. This will take at least 10 min to download all the workflow images. Maybe write a nice review on `image.sc` of this software, or here on the `Discussions` tab of Github.
    3. Select your image / dataset and run script `slurm/workflows/SLURM Run Workflow...`
        - Select at least one of the `Select how to import your results`, e.g. change `Import into NEW Dataset` text to `hello world`
        - Select a fun workflow, e.g. `cellpose`.
            - Change the `nuc channel` to the channel to segment (note that 0 is for grey, so 1,2,3 for RGB)
            - Uncheck the `use gpu` (step 2, our HPC cluster, doesn't come with GPU support built into the containers)
        - Refresh your OMERO `Explore` tab to see your `hello world` dataset with a mask image when the workflow is done!



# Prerequisites & Getting Started with BIOMERO

## Slurm Requirements
Note: This library has only been tested on Slurm versions 21.08.6 and 22.05.09 !

Your Slurm cluster/login node needs to have:
1. SSH access w/ public key (headless)
2. SCP access (generally comes with SSH)
3. 7zip installed
4. Singularity/Apptainer installed
5. (Optional) Git installed, if you want your own job scripts

## OMERO Requirements

Your OMERO _processing_ node needs to have:
1. SSH client and access to the Slurm cluster (w/ private key / headless)
2. SCP access to the Slurm cluster
3. Python3.7+
4. This library installed 
    - Latest release on PyPI `python3 -m pip install biomero`
    - or latest Github version `python3 -m pip install 'git+https://github.com/NL-BioImaging/biomero'`
5. Configuration setup at `/etc/slurm-config.ini`
6. Requirements for some scripts: `python3 -m pip install ezomero==1.1.1 tifffile==2020.9.3` and the [OMERO CLI Zarr plugin](https://github.com/ome/omero-cli-zarr).

Your OMERO _server_ node needs to have:
1. Some OMERO example scripts installed to interact with this library:
    - My examples on github: `https://github.com/NL-BioImaging/biomero-scripts`
    - Install those at `/opt/omero/server/OMERO.server/lib/scripts/slurm/`, e.g. `git clone https://github.com/NL-BioImaging/biomero-scripts.git <path>/slurm`

!!*NOTE*: Do not install [Example Minimal Slurm Script](https://github.com/NL-BioImaging/biomero-scripts/blob/master/Example_Minimal_Slurm_Script.py) if you do not trust your users with your Slurm cluster. It has literal Command Injection for the SSH user as a **FEATURE**. 




## Getting Started

To connect an OMERO processor to a Slurm cluster using the `biomero` library, users can follow these steps:

1. Setup passwordless public key authentication between your OMERO `processor` server and your HPC server. E.g. follow  a [SSH tutorial](https://www.ssh.com/academy/ssh/public-key-authentication) or [this one](https://linuxize.com/post/how-to-setup-passwordless-ssh-login/).
    - You could use 1 Slurm account for all `processor` servers, and share the same private key to all of them.
    - Or you could use unique accounts, but give them all the same alias in step 2.

2. Create a SSH config file named `config` in the `.ssh` directory of (all) the OMERO `processor` servers, within the `omero` user's home directory (`~/.ssh/config`). This file should specify the hostname, username, port, and private key path for the Slurm cluster, under some alias. This alias we will provide to the library. We provide an example in the [resources](./resources/config) directory.

    - This will allow a uniform SSH naming, and makes the connection headless; making it easy for the library.

    - Test the SSH connection manually! `ssh slurm` (as the omero user) should connect you to the Slurm server (given that you named it `slurm` in the `config`).

    - Congratulations! Now the servers are connected. Next, we make sure to setup the connection between OMERO and Slurm.

3. At this point, ensure that the `slurm-config.ini` file is correctly configured with the necessary SSH and Slurm settings, including the host, data path, images path, and model details. Customize the configuration according to the specific Slurm cluster setup. We provide an example in the [resources](./resources/slurm-config.ini) section. To read it automatically, place this `ini` file in one of the following locations (on the OMERO `processor` server):
    - `/etc/slurm-config.ini`
    - `~/slurm-config.ini`

4. Install OMERO scripts from [OMERO Slurm Scripts](https://github.com/NL-BioImaging/biomero-scripts), e.g. 
    - `cd OMERO_DIST/lib/scripts`
    - `git clone https://github.com/NL-BioImaging/biomero-scripts.git slurm`

!!*NOTE*: Do not install [Example Minimal Slurm Script](https://github.com/NL-BioImaging/biomero-scripts/blob/master/Example_Minimal_Slurm_Script.py) if you do not trust your users with your Slurm cluster. It has literal Command Injection for the SSH user as a **FEATURE**. 

5. Install [BIOMERO Scripts](https://github.com/NL-BioImaging/biomero-scripts/) requirements, e.g.
    - `python3 -m pip install ezomero==1.1.1 tifffile==2020.9.3` 
    - the [OMERO CLI Zarr plugin](https://github.com/ome/omero-cli-zarr), e.g. 
    `python3 -m pip install omero-cli-zarr==0.5.3` && `yum install -y blosc-devel`
    - the [bioformats2raw-0.7.0](https://github.com/glencoesoftware/bioformats2raw/releases/download/v0.7.0/bioformats2raw-0.7.0.zip), e.g. `unzip -d /opt bioformats2raw-0.7.0.zip && export PATH="$PATH:/opt/bioformats2raw-0.7.0/bin"`

6. To finish setting up your `SlurmClient` and Slurm server, run it once with `init_slurm=True`. This is provided in a OMERO script form at [init/Slurm Init environment](https://github.com/NL-BioImaging/biomero-scripts/blob/master/init/SLURM_Init_environment.py) , which you just installed in previous step.
    - Provide the configfile location explicitly if it is not a default one defined earlier, otherwise you can omit that field. 
    - Please note the requirements for your Slurm cluster. We do not install Singularity / 7zip on your cluster for you (at the time of writing).
    - This operation will make it create the directories you provided in the `slurm-config.ini`, pull any described Singularity images to the server (note: might take a while), and generate (or clone from Git) any job scripts for these workflows:

```python
with SlurmClient.from_config(configfile=configfile,
                            init_slurm=True) as slurmClient:
    slurmClient.validate(validate_slurm_setup=True)
```

With the configuration files in place, you can utilize the `SlurmClient` class from the `biomero` library to connect to the Slurm cluster over SSH, enabling the submission and management of Slurm jobs from an OMERO processor. 

# BIOMERO scripts

The easiest interaction from OMERO with this library currently is through our BIOMERO scripts, which are just a set of OMERO scripts using this library for all the steps one needs to run a image analysis workflow from OMERO on Slurm and retrieve the results back into OMERO.

!!*NOTE*: Do not install [Example Minimal Slurm Script](https://github.com/NL-BioImaging/biomero-scripts/blob/master/Example_Minimal_Slurm_Script.py) if you do not trust your users with your Slurm cluster. It has literal Command Injection for the SSH user as a **FEATURE**. 

We have provided the BIOMERO scripts at https://github.com/NL-BioImaging/biomero-scripts (hopefully installed in a previous step). 

For example, [workflows/Slurm Run Workflow](https://github.com/NL-BioImaging/biomero-scripts/blob/master/workflows/SLURM_Run_Workflow.py) should provide an easy way to send data to Slurm, run the configured and chosen workflow, poll Slurm until jobs are done (or errors) and retrieve the results when the job is done. This workflow script uses some of the other scripts, like

-  [`data/Slurm Image Transfer`](https://github.com/NL-BioImaging/biomero-scripts/blob/master/data/_SLURM_Image_Transfer.py): to export your selected images / dataset / screen as TIFF files to a Slurm dir.
- [`data/Slurm Get Results`](https://github.com/NL-BioImaging/biomero-scripts/blob/master/data/SLURM_Get_Results.py): to import your Slurm job results back into OMERO as a zip, dataset or attachment.

Other example OMERO scripts are:
- [`data/Slurm Get Update`](https://github.com/NL-BioImaging/biomero-scripts/blob/master/data/SLURM_Get_Update.py): to run while you are waiting on a job to finish on Slurm; it will try to get a `%` progress from your job's logfile. Depends on your job/workflow logging a `%` of course.

- [`workflows/Slurm Run Workflow Batched`](https://github.com/NL-BioImaging/biomero-scripts/blob/master/workflows/SLURM_Run_Workflow_Batched.py): This will allow you to run several `workflows/Slurm Run Workflow` in parallel, by batching your input images into smaller chunks (e.g. turn 64 images into 2 batches of 32 images each). It will then poll all these jobs.

- [`workflows/Slurm CellPose Segmentation`](https://github.com/NL-BioImaging/biomero-scripts/blob/master/workflows/SLURM_CellPose_Segmentation.py): This is a more primitive script that only runs the actual workflow `CellPose` (if correctly configured). You will need to manually transfer data first (with `Slurm Image Transfer`) and manually retrieve data afterward (with `Slurm Get Results`).

You are encouraged to create your own custom scripts. Do note the copy-left license enforced by OME.

# (Docker) containers
We host BIOMERO container dockerfiles at [NL-BIOMERO](https://github.com/Cellular-Imaging-Amsterdam-UMC/NL-BIOMERO), which publishes container images to our public dockerhub [cellularimagingcf](https://hub.docker.com/repositories/cellularimagingcf). Specifically the [cellularimagingcf/biomero](https://hub.docker.com/repository/docker/cellularimagingcf/biomero/general) image is an OMERO processor container with BIOMERO library installed. When we release a new version of BIOMERO, we will also release a new version of these containers (because we deploy these locally at our Core Facility - Cellular Imaging).

You can mount your specific configurations over those in the container, for example:

```
# Run the biomero container
echo "Starting BIOMERO..."
podman run -d --rm --name biomero \
  -e CONFIG_omero_master_host=omeroserver \
  -e OMERO_WORKER_NAME=biomero \
  -e CONFIG_omero_logging_level=10 \
  --network omero \
  --volume /mnt/datadisk/omero:/OMERO \
  --volume /mnt/data:/data \
  --volume /my/slurm-config.ini:/etc/slurm-config.ini \
  --secret ssh-config,target=/tmp/.ssh/config --secret ssh-key,target=/tmp/.ssh/id_rsa --secret ssh-pubkey,target=/tmp/.ssh/id_rsa.pub  --secret ssh-known_hosts,target=/tmp/.ssh/known_hosts \
  --userns=keep-id:uid=1000,gid=997 \
  cellularimagingcf/biomero:0.2.3
```

This will spin up the docker container (in Podman) with omero config (`-e CONFIG_omero_..`), mounting the required data drives (`--volume /mnt/...`) and adding a new slurm config (`--volume /my/slurm-config.ini:/etc/slurm-config.ini`) and the required SSH settings (`--secret ...,target=/tmp/.ssh/...`) to access the remote HPC.

Note: the [BIOMERO scripts](https://github.com/NL-BioImaging/biomero-scripts) are installed on the [main server](https://hub.docker.com/repository/docker/cellularimagingcf/omeroserver/general), not on the BIOMERO processor. 

Note2: We will also update these containers with our own desired changes, so they will likely not be 1:1 copy with basic omero containers. Especially when we start making a nicer UI for BIOMERO. We will keep up-to-date with the OMERO releases when possible.

# See the tutorials
I have also provided tutorials on connecting to a Local or Cloud Slurm, and tutorials on how to add your FAIR workflows to this setup. Those can give some more insights as well.

# SSH
Note: this library is built for **SSH-based connections**. If you could, it would be a lot easier to just have the OMERO `processor` server and the `slurm` client server be (on) the same machine: then you can just directly call `sbatch` and other `slurm` commands from OMERO scripts and Slurm would have better access to your data. 

This is mainly for those cases where you already have an external HPC cluster and want to connect your OMERO instance.

Theoretically, you could extend the `SlurmClient` class and change the `run` commands to not use SSH, but just a `subprocess`. We might implement this if we need it in the future.
But then you could also look at other Python libraries like [submitit](https://github.com/facebookincubator/submitit).

# SlurmClient class
The SlurmClient class is the main entrypoint in using this library.
It is a Python class that extends the Connection class from the Fabric library. It allows connecting to and interacting with a Slurm cluster over SSH. 

It includes attributes for specifying paths to directories for Slurm data and Singularity images, as well as specific paths, repositories, and Dockerhub information for different Singularity image models. 

The class provides methods for running commands on the remote Slurm host, submitting jobs, checking job status, retrieving job output, and tailing log files. 

It also offers a `from_config` class method to create a `SlurmClient` object by reading configuration parameters from a file. Overall, the class provides a convenient way to work with Slurm clusters and manage job execution and monitoring.


# slurm-config.ini
The `slurm-config.ini` file is a configuration file used by the `biomero` Python package to specify various settings related to SSH and Slurm. Here is a brief description of its contents:

[**SSH**]: This section contains SSH settings, including the alias for the SLURM SSH connection (host). Additional SSH configuration can be specified in the user's SSH config file or in `/etc/fabric.yml`.

[**SLURM**]: This section includes settings specific to Slurm. It defines the paths on the SLURM entrypoint for storing data files (slurm_data_path), container image files (slurm_images_path), and Slurm job scripts (slurm_script_path). It also specifies the repository (slurm_script_repo) from which to pull the Slurm scripts.

[**MODELS**]: This section is used to define different model settings. Each model has a unique key and requires corresponding values for `<key>_repo` (repository containing the descriptor.json file, which will describe parameters and where to find the image), and `<key>_job` (jobscript name and location in the `slurm_script_repo`). The example shows settings for several segmentation models, including Cellpose, Stardist, CellProfiler, DeepCell, and ImageJ.

The `slurm-config.ini` file allows users to configure paths, repositories, and other settings specific to their Slurm cluster and the `biomero` package, providing flexibility and customization options.

# How to add an existing workflow

To add an existing (containerized) workflow, add it to the `slurm-config.ini` file like in our example:
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
```
Here, 
1. the name referenced for this workflow is `cellpose`
2. the location of the container on slurm will be `<slurm_images_path>/cellpose`
3. the code repository is `https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose` 
4. the specific version we want is `v1.2.7`
5. the container can be found on bitbucket
    - under the path given in the metadata file: [descriptor.json](https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose/blob/v1.2.7/descriptor.json)
5. the location of the jobscript on slurm will be `<slurm_script_repo>/jobs/cellpose.sh`. 
    - This either references a git repo, where it matches this path, 
    - or it will be the location where the library will generate a jobscript (if no repo is given)

## Workflow metadata via descriptor.json
A lot of the automation in this library is based on metadata of the workflow, provided in the source code of the workflow, specifically the [descriptor.json](https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose/blob/v1.2.7/descriptor.json).

For example, the OMERO script UI can be generated automatically, based on this descriptor. And also, the Slurm job script can be generated automatically, based on this descriptor.

This metadata scheme is (based on) Cytomine / BIAFLOWS, and you can find details of it and how to create one yourself on their website, e.g. this [Cytomine dev-guide](https://doc.uliege.cytomine.org/dev-guide/algorithms/write-app#create-the-json-descriptor) or this [BIAFLOWS dev-guide](https://neubias-wg5.github.io/developer_guide_add_new_workflow_to_biaflows_instance.html).

**NOTE!** We do not require the `cytomine_<...>` authentication parameters. They are not mandatory. In fact, we ignore them. But it might be beneficial to make your workflow compatible with Cytomine as well.

### Schema
At this point, we are using the `cytomine-0.1` [schema](https://doc.uliege.cytomine.org/dev-guide/algorithms/descriptor-reference), in the future we will also want to support other schemas, like [Boutiques](https://boutiques.github.io/), [commonwl](https://www.commonwl.org/) or [MLFlow](https://www.mlflow.org/docs/latest/projects.html). 

We will try to stay compatible with all such schemas (perhaps with less functionality because of missing metadata).

At this point, we do not strictly validate the schema, we just read expected fields from the `descriptor.json`.

## Multiple versions
Note that while it is possible to have multiple versions of the same workflow on Slurm (and select the desired one in OMERO), it is not possible to configure this yet. We assume for now you only want one version to start with. You can always update this config to download a new version to Slurm.

## I/O
Unless you change the `Slurm` job, the input is expected to be:
- The `infolder` parameter
    - pointing to a folder with multiple input files/images
- The `gtfolder` parameter (Optional)
    - pointing to a `ground-truth` input files, generally not needed for prediction / processing purposes.
- The `outfolder` parameter
    - where you write all your output files (to get copied back to OMERO)

### Wrapper.py
Note that you can also use the [wrapper.py](https://github.com/Neubias-WG5/W_Template/blob/master/wrapper.py) setup from BIAFLOWS to handle the I/O for you: 

```python
with BiaflowsJob.from_cli(argv) as bj:
        # Change following to the actual problem class of the workflow
        ...
        
        # 1. Prepare data for workflow
        in_imgs, gt_imgs, in_path, gt_path, out_path, tmp_path = prepare_data(problem_cls, bj, is_2d=True, **bj.flags)

        # 2. Run image analysis workflow
        bj.job.update(progress=25, statusComment="Launching workflow...")

        # Add here the code for running the analysis script

        # 3. Upload data to BIAFLOWS
        ...
        
        # 4. Compute and upload metrics
        ...

        # 5. Pipeline finished
        ...
```

This wrapper handles the input parameters for you, providing the input images as `in_imgs`, et cetera. Then you add your commandline call between point 2 and 3, and possibly some preprocessing between point 1 and 2:
```python
#add here the code for running the analysis script
```

For example, from [Cellpose](https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose/blob/master/wrapper.py) container workflow:
```python
...

# 2. Run image analysis workflow
bj.job.update(progress=25, statusComment="Launching workflow...")

# Add here the code for running the analysis script
prob_thresh = bj.parameters.prob_threshold
diameter = bj.parameters.diameter
cp_model = bj.parameters.cp_model
use_gpu = bj.parameters.use_gpu
print(f"Chosen model: {cp_model} | Channel {nuc_channel} | Diameter {diameter} | Cell prob threshold {prob_thresh} | GPU {use_gpu}")
cmd = ["python", "-m", "cellpose", "--dir", tmp_path, "--pretrained_model", f"{cp_model}", "--save_tif", "--no_npy", "--chan", "{:d}".format(nuc_channel), "--diameter", "{:f}".format(diameter), "--cellprob_threshold", "{:f}".format(prob_thresh)]
if use_gpu:
    print("Using GPU!")
    cmd.append("--use_gpu")
status = subprocess.run(cmd)

if status.returncode != 0:
    print("Running Cellpose failed, terminate")
    sys.exit(1)

# Crop to original shape
for bimg in in_imgs:
    shape = resized.get(bimg.filename, None)
    if shape:
        img = imageio.imread(os.path.join(tmp_path,bimg.filename_no_extension+"_cp_masks.tif"))
        img = img[0:shape[0], 0:shape[1]]
        imageio.imwrite(os.path.join(out_path,bimg.filename), img)
    else:
        shutil.copy(os.path.join(tmp_path,bimg.filename_no_extension+"_cp_masks.tif"), os.path.join(out_path,bimg.filename))

# 3. Upload data to BIAFLOWS
```
We get the commandline parameters from `bj.parameters` (biaflows job) and provide that the `cmd` commandline string. Then we run it with `subprocess.run(cmd)` and check the `status`. 

We use a `tmp_path` to store both input and output, then move the output to the `out_path` after the processing is done.

Also note that some preprocessing is done in step 1: 
```python
# Make sure all images have at least 224x224 dimensions
# and that minshape / maxshape * minshape >= 224
# 0 = Grayscale (if input RGB, convert to grayscale)
# 1,2,3 = rgb channel
nuc_channel = bj.parameters.nuc_channel
resized = {}
for bfimg in in_imgs:
    ...
    imageio.imwrite(os.path.join(tmp_path, bfimg.filename), img)
```

Another example is this `imageJ` [wrapper](https://github.com/Neubias-WG5/W_NucleiSegmentation3D-ImageJ/blob/master/wrapper.py):
```python
...

# 3. Call the image analysis workflow using the run script
nj.job.update(progress=25, statusComment="Launching workflow...")

command = "/usr/bin/xvfb-run java -Xmx6000m -cp /fiji/jars/ij.jar ij.ImageJ --headless --console " \
            "-macro macro.ijm \"input={}, output={}, radius={}, min_threshold={}\"".format(in_path, out_path, nj.parameters.ij_radius, nj.parameters.ij_min_threshold)
return_code = call(command, shell=True, cwd="/fiji")  # waits for the subprocess to return

if return_code != 0:
    err_desc = "Failed to execute the ImageJ macro (return code: {})".format(return_code)
    nj.job.update(progress=50, statusComment=err_desc)
    raise ValueError(err_desc)
    
```
Once again, just a commandline `--headless` call to `ImageJ`, wrapped in this Python script and this container.


# How to add your new custom workflow
Building workflows like this will make them more [FAIR](https://www.go-fair.org/fair-principles/) (also for [software](https://fair-software.eu/about)) and uses best practices like code versioning and containerization!

Also take a look at our in-depth tutorial on adding a Cellprofiler pipeline as a workflow to BIOMERO.

Here is a shorter version:
Say you have a script in Python and you want to make it available on OMERO and Slurm.

These are the steps required:

1. Rewrite your script to be headless / to be executable on the commandline. This requires handling of commandline parameters as input.
    - Make sure the I/O matches the Slurm job, see [previous chapter](#io).
2. Describe these commandline parameters in a `descriptor.json` (see previous [chapter](#workflow-metadata-via-descriptorjson)). E.g. [like this](https://doc.uliege.cytomine.org/dev-guide/algorithms/write-app#create-the-json-descriptor).
3. Describe the requirements / environment of your script in a `requirements.txt`, [like this](https://learnpython.com/blog/python-requirements-file/). Make sure to pin your versions for future reproducability!
2. Package your script in a Docker container. E.g. [like this](https://www.docker.com/blog/how-to-dockerize-your-python-applications/).
    - Note: Please watch out for the pitfalls of reproducability with Dockerfiles: [Always version your packages!](https://pythonspeed.com/articles/dockerizing-python-is-hard/).
3. Publish your source code, Dockerfile and descriptor.json to a new Github repository (free for public repositories). You can generate a new repository [from template](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template), using [this template](https://github.com/Neubias-WG5/W_Template) provided by Neubias (BIAFLOWS). Then replace the input of the files with yours.
4. (Recommended) Publish a new version of your code (e.g. v1.0.0). E.g. [like this](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).
5. Publish your container on Dockerhub (free for public repositories), using the same versioning as your source code. [Like this](https://docs.docker.com/get-started/publish-your-own-image/) from Windows Docker or [like this](https://www.geeksforgeeks.org/docker-publishing-images-to-docker-hub/) from a commandline.
    - (Recommended) Please use a tag that equals your repository version, instead of `latest`. This improves reproducability!
    - (Optional) this library grabs `latest` if the code repository is given no version, but the `master` branch.
6. Follow the steps from the previous [chapter](#how-to-add-an-existing-workflow):
    - Add details to `slurm-config.ini`
    - Run `SlurmClient.from_config(init_slurm=True)` (e.g. the init environment script.)

# Slurm jobs

## Generating jobs
By default, `biomero` will generate basic slurm jobs for each workflow, based on the metadata provided in `descriptor.json` and a [job template](./resources/job_template.sh).
It will replace `$PARAMS` with the (non-`cytomine_`) parameters given in `descriptor.json`. See also the [Parameters](#parameters) section below.

## How to add your own Slurm job
You could change the [job template](./resources/job_template.sh) and generate new jobs, by running `SlurmClient.from_config(init_slurm=True)` (or `slurmClient.update_slurm_scripts(generate_jobs=True)`) 

Or you could add your jobs to a [Github repository](https://github.com/TorecLuik/slurm-scripts) and reference this in `slurm-config.ini`, both in the field `slurm_script_repo` and every `<workflow>_job`:

```ini
# -------------------------------------
# REPOSITORIES
# -------------------------------------
# A (github) repository to pull the slurm scripts from.
#
# Note: 
# If you provide no repository, we will generate scripts instead!
# Based on the job_template and the descriptor.json
#
slurm_script_repo=https://github.com/TorecLuik/slurm-scripts

[MODELS]
# -------------------------------------
# Model settings
# -------------------------------------
# ...
# -------------------------------------
# CELLPOSE SEGMENTATION
# -------------------------------------
# The path to store the container on the slurm_images_path
cellpose=cellpose
# The (e.g. github) repository with the descriptor.json file
cellpose_repo=https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose/tree/v1.2.7
# The jobscript in the 'slurm_script_repo'
cellpose_job=jobs/cellpose.sh
```

You can update the jobs by calling `slurmClient.update_slurm_scripts()`, which will pull the repository('s default branch).

This might be useful, for example if you have other hardware requirements for your workflow(s) than the default job asks for, or if you want to run more than just 1 singularity container.

### Parameters
The library will provide the parameters from your `descriptor.json` as environment variables to the call. E.g. `set DIAMETER=0; sbatch ...`.

Other environment variables provided are:
- `DATA_PATH` 
    - Made of `<slurm_data_path>/<input_folder>`. The base dir for data folders for this execution. We expect it to contain `/data/in`, `/data/in` and `/data/in` folders in our template and data transfer setup.
- `IMAGE_PATH`
    - Made of `<slurm_images_path>/<model_path>`, as described in `slurm-config.ini`
- `IMAGE_VERSION`
- `SINGULARITY_IMAGE`
    - Already uses the `IMAGE_VERSION` above, as `<container_name>_<IMAGE_VERSION>.sif`

We (potentially) override the following Slurm job settings programmatically:
- `--mail-user={email}` (optional)
- `--time={time}` (optional)
- `--output=omero-%4j.log` (mandatory)

We could add more overrides in the future, and perhaps make them available as global configuration variables in `slurm-config.ini`.
# Batching
We can simply use `Slurm` for running your workflow 1:1, so 1 job to 1 workflow. This could speed up your workflow already, as `Slurm` servers are likely equipped with strong CPU and GPU.

However, `Slurm` is also built for parallel processing on multiple (or the same) servers. We can accomplish this by running multiple jobs for 1 workflow. This is simple for [embarrassingly parallel](https://en.wikipedia.org/wiki/Embarrassingly_parallel#:~:text=In%20parallel%20computing%2C%20an%20embarrassingly,a%20number%20of%20parallel%20tasks.) tasks, like segmenting multiple images: just provide each job with a different set of input images. If you have 100 images, you could run 10 jobs on 10 images and (given enough resources available for you on Slurm) that could be 10x faster. In theory, you could run 1 job per image, but at some point you run into the overhead cost of Slurm (and OMERO) and it might actually slow down again (as you incur this cost a 100 times instead of 10 times).

# Using the GPU on Slurm

Note, the [default](./resources/job_template.sh) Slurm job script will not request any GPU resources.

This is because GPU resources are expensive and some programs do not work with GPU.

We can instead _enable_ the use of GPU by either providing our own Slurm job scripts, or setting an override value in `slurm-config.ini`:

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
```

In fact, any `..._job_...=...` configuration value will be forwarded to the Slurm commandline.

Slurm commandline parameters override those in the script, so the above one requests 1 10GB gpu for Cellpose.

E.g. you could also set the time limit higher:

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
# Run with longer time limit
cellpose_job_time=00:30:00
```

Now the CellPose job should run for maximum of 30 minutes, instead of the default.

# Transfering data

We have added methods to this library to help with transferring data to the `Slurm` cluster, using the same SSH connection (via SCP or SFTP).

- `slurmClient.transfer_data(...)`
    - Transfer data to the Slurm cluster
- `slurmClient.unpack_data(...)`
    - Unpack zip file on the Slurm cluster
- `slurmClient.zip_data_on_slurm_server(...)`
    - Zip data on the Slurm cluster
- `slurmClient.copy_zip_locally(...)`
    - Transfer (zip) data from the Slurm cluster
- `slurmClient.get_logfile_from_slurm(...)`
    - Transfer logfile from the Slurm cluster

And more; see the docstring of `SlurmClient` and example OMERO scripts.

# Testing the Python code
You can test the library by installing the extra test dependencies:

1. Create a venv to isolate the python install:
`python -m venv venvTest`

2. Install OSC with test dependencies:
`venvTest/Scripts/python -m pip install .[test]`

3. Run pytest from this venv:
`venvTest/Scripts/pytest`

# Logging
Debug logging can be enabled with the standard python logging module, for example with logging.basicConfig():

```
import logging

logging.basicConfig(level='DEBUG')
```

For example in (the `__init__` of) a script:

```Python
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        stream=sys.stdout)
    runScript()
```
