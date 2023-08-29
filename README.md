# Omero Slurm Client library
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![DOI](https://zenodo.org/badge/638954891.svg)](https://zenodo.org/badge/latestdoi/638954891) ![Python](https://img.shields.io/badge/Python-3.6-blue.svg) ![Slurm](https://img.shields.io/badge/Slurm-21.08.6-blue.svg) ![Omero](https://img.shields.io/badge/Omero-5.6.8-blue.svg) [![fair-software.eu](https://img.shields.io/badge/fair--software.eu-%E2%97%8F%20%20%E2%97%8F%20%20%E2%97%8B%20%20%E2%97%8F%20%20%E2%97%8F-yellow)](https://fair-software.eu) [![OpenSSF Best Practices](https://bestpractices.coreinfrastructure.org/projects/7530/badge)](https://bestpractices.coreinfrastructure.org/projects/7530) [![Sphinx build](https://github.com/NL-BioImaging/omero-slurm-client/actions/workflows/sphinx.yml/badge.svg?branch=main)](https://github.com/NL-BioImaging/omero-slurm-client/actions/workflows/sphinx.yml) [![pages-build-deployment](https://github.com/NL-BioImaging/omero-slurm-client/actions/workflows/pages/pages-build-deployment/badge.svg)](https://github.com/NL-BioImaging/omero-slurm-client/actions/workflows/pages/pages-build-deployment)

The `omero_slurm_client` Python package is a library that facilitates working with a Slurm cluster in the context of the Omero platform. 

The package includes the `SlurmClient` class, which extends the Fabric library's `Connection` class to provide **SSH-based connectivity** and interaction with a Slurm cluster. The package enables users to submit jobs, monitor job status, retrieve job output, and perform other Slurm-related tasks. 

Additionally, the package offers functionality for configuring and managing paths to Slurm data and Singularity images, as well as specific image models and their associated repositories. 

Overall, the `omero_slurm_client` package simplifies the integration of Slurm functionality within the Omero platform and provides an efficient workflow for working with Slurm clusters.

# SSH
Note: this library is built for SSH-based connections. If you could, it would be a lot easier to just have the Omero `processor` server and the `slurm` client server be (on) the same computer: then you can just directly call `sbatch` and other `slurm` commands from Omero scripts.

This is for those cases where you already have an external HPC cluster and want to connect your Omero instance, but cannot connect it so directly by joining the HPC cluster.

Despite that, the approach to workflows and control via a Python API in this library could still be interesting to those with direct access to a `slurm` cluster. You could extend the `SlurmClient` class and change the `run` commands to not use SSH, but just a `subprocess`. 
But you could also look at other python libraries like [submitit](https://github.com/facebookincubator/submitit).

# SlurmClient class
The SlurmClient class is the main entrypoint in using this library.
It is a Python class that extends the Connection class from the Fabric library. It allows connecting to and interacting with a Slurm cluster over SSH. 

It includes attributes for specifying paths to directories for Slurm data and Singularity images, as well as specific paths, repositories, and Dockerhub information for different Singularity image models. 

The class provides methods for running commands on the remote Slurm host, submitting jobs, checking job status, retrieving job output, and tailing log files. 

It also offers a `from_config` class method to create a `SlurmClient` object by reading configuration parameters from a file. Overall, the class provides a convenient way to work with Slurm clusters and manage job execution and monitoring.

# Prerequisites & Getting Started

## Slurm Requirements
Note: This library has only been tested on Slurm version 21.08.6 and 22.05.09 !

Your Slurm cluster/login node needs to have:
1. SSH access w/ public key (headless)
2. SCP access (generally comes with SSH)
3. 7zip
4. Singularity/Apptainer
5. (Optional) Git

## Omero Requirements

Your Omero _processing_ node needs to have:
1. SSH client and access to the Slurm cluster (w/ private key / headless)
2. SCP access to the Slurm cluster
3. Python3.6+
4. This library installed (`python3 -m pip install 'git+https://github.com/NL-BioImaging/omero-slurm-client'`)
5. Configuration setup at `/etc/slurm-config.ini`
6. (Optional) requirements for some scripts: `python3 -m pip install ezomero==1.1.1 tifffile==2020.9.3`

Your Omero _server_ node needs to have:
1. Some Omero example scripts installed to interact with this library:
    - My examples on github: `https://github.com/TorecLuik/docker-example-omero-grid-amc/tree/processors/scripts/slurm`
    - Install those `scripts/slurm/*` at `/opt/omero/server/OMERO.server/lib/scripts/slurm/` (or make some more subfolders)




## Getting Started

To connect an Omero processor to a Slurm cluster using the `omero_slurm_client` library, users can follow these steps:

1. Setup passwordless public key authentication between your Omero `processor` server and your HPC server. E.g. follow  a [SSH tutorial](https://www.ssh.com/academy/ssh/public-key-authentication) or [this one](https://linuxize.com/post/how-to-setup-passwordless-ssh-login/).
    - You could use 1 Slurm account for all `processor` servers, and share the same private key to all of them.
    - Or you could use unique accounts, but give them all the same alias in step 2.

2. Create a SSH config file named `config` in the `.ssh` directory of (all) the Omero `processor` servers, within the `omero` user's home directory (`~/.ssh/config`). This file should specify the hostname, username, port, and private key path for the Slurm cluster, under some alias. This alias we will provide to the library. We provide an example in the [resources](./resources/config) directory.

    - This will allow a uniform SSH naming, and makes the connection headless; making it easy for the library.

3. Test the SSH connection manually! `ssh slurm` (as the omero user) should connect you to the Slurm server (given that you named it `slurm` in the `config`).

4. Congratulations! Now the servers are connected. Next, we make sure to setup the connection between Omero and Slurm.

2. At this point, ensure that the `slurm-config.ini` file is correctly configured with the necessary SSH and Slurm settings, including the host, data path, images path, and model details. Customize the configuration according to the specific Slurm cluster setup. We provide an example in the [resources](./resources/slurm-config.ini) section. To read it automatically, place this `ini` file in one of the following locations (on the Omero `processor` server):
    - `/etc/slurm-config.ini`
    - `~/slurm-config.ini`


6. To finish setting up your `SlurmClient` and Slurm server, run it once with `init_slurm=True`. Provide the configfile location explicitly if it is not a default one from the previous step, otherwise you can omit it. This operation will make it create the directories you provided in the `slurm-config.ini`, pull any described Singularity images to the server (note: might take a while), and generate (or clone from Git) any job scripts for these workflows:

```python
with SlurmClient.from_config(configfile=configfile,
                            init_slurm=True) as slurmClient:
    slurmClient.validate(validate_slurm_setup=True)
```
7. With the configuration files in place, users can utilize the `SlurmClient` class from the Omero-Slurm library to connect to the Slurm cluster over SSH, enabling the submission and management of Slurm jobs from an Omero processor.

I have also provided a tutorial on connecting to a Local or Cloud Slurm. Those can give some more insights as well.


# slurm-config.ini
The `slurm-config.ini` file is a configuration file used by the `omero_slurm_client` Python package to specify various settings related to SSH and Slurm. Here is a brief description of its contents:

[**SSH**]: This section contains SSH settings, including the alias for the SLURM SSH connection (host). Additional SSH configuration can be specified in the user's SSH config file or in `/etc/fabric.yml`.

[**SLURM**]: This section includes settings specific to Slurm. It defines the paths on the SLURM entrypoint for storing data files (slurm_data_path), container image files (slurm_images_path), and Slurm job scripts (slurm_script_path). It also specifies the repository (slurm_script_repo) from which to pull the Slurm scripts.

[**MODELS**]: This section is used to define different model settings. Each model has a unique key and requires corresponding values for `<key>_repo` (repository containing the descriptor.json file, which will describe parameters and where to find the image), and `<key>_job` (jobscript name and location in the `slurm_script_repo`). The example shows settings for several segmentation models, including Cellpose, Stardist, CellProfiler, DeepCell, and ImageJ.

The `slurm-config.ini` file allows users to configure paths, repositories, and other settings specific to their Slurm cluster and the `omero_slurm_client` package, providing flexibility and customization options.

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

For example, the Omero script UI can be generated automatically, based on this descriptor. And also, the Slurm job script can be generated automatically, based on this descriptor.

This metadata scheme is (based on) Cytomine / BIAFLOWS, and you can find details of it and how to create one yourself on their website, e.g. this [Cytomine dev-guide](https://doc.uliege.cytomine.org/dev-guide/algorithms/write-app#create-the-json-descriptor) or this [BIAFLOWS dev-guide](https://neubias-wg5.github.io/developer_guide_add_new_workflow_to_biaflows_instance.html).

**NOTE!** We do not require the `cytomine_<...>` authentication parameters. They are not mandatory. In fact, we ignore them. But it might be beneficial to make your workflow compatible with Cytomine as well.

### Schema
At this point, we are using the `cytomine-0.1` [schema](https://doc.uliege.cytomine.org/dev-guide/algorithms/descriptor-reference), but we could diverge from it in the future if we need to support other fields/datatypes. For example, Cytomine 0.1 uses Number for both Integer and Double, which is not nice in Python. For now, we handle that internally, to stay compatible with Cytomine apps.

At this point, we also do not validate the schema, we just read some expected fields from the `descriptor.json`.

## Multiple versions
Note that while it is possible to have multiple versions of the same workflow on Slurm (and select the desired one in Omero), it is not possible to configure this yet. We assume for now you only want one version to start with. You can always update this config to download a new version to Slurm.

## I/O
Unless you change the `Slurm` job, the input is expected to be:
- The `infolder` parameter
    - pointing to a folder with multiple input files/images
- The `gtfolder` parameter (Optional)
    - pointing to a `ground-truth` input files, generally not needed for prediction / processing purposes.
- The `outfolder` parameter
    - where you write all your output files (to get copied back to Omero)

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

Also take a look at our in-depth tutorial on adding a Cellprofiler pipeline as a workflow to Omero Slurm Client.

Here is a shorter version:
Say you have a script in Python and you want to make it available on Omero and Slurm.

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
    - Run `SlurmClient.from_config(init_slurm=True)`

# Slurm jobs

## Generating jobs
By default, `omero_slurm_client` will generate basic slurm jobs for each workflow, based on the metadata provided in `descriptor.json` and a [job template](./resources/job_template.sh).
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

However, `Slurm` is also built for parallel processing on multiple (or the same) servers. We can accomplish this by running multiple jobs for 1 workflow. This is simple for [embarrassingly parallel](https://en.wikipedia.org/wiki/Embarrassingly_parallel#:~:text=In%20parallel%20computing%2C%20an%20embarrassingly,a%20number%20of%20parallel%20tasks.) tasks, like segmenting multiple images: just provide each job with a different set of input images. If you have 100 images, you could run 10 jobs on 10 images and (given enough resources available for you on Slurm) that could be 10x faster. In theory, you could run 1 job per image, but at some point you run into the overhead cost of Slurm (and Omero) and it might actually slow down again (as you incur this cost a 100 times instead of 10 times).

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

And more; see the docstring of `SlurmClient` and example Omero scripts.