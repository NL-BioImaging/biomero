# Cellprofiler tutorial

Cellprofiler is already known for excellent interoperability with OMERO. You can directly load images into the cellprofiler pipelines. 

Cellprofiler also has options to run in batch mode and headless, for analyzing big data on compute clusters, like we want as well.

However, for our purposes, this is insufficient, as we want to run it from OMERO, and on a compute cluster that only has SSH access. 

In this tutorial I will show you how to add a cellprofiler pipeline as a workflow to OMERO and Slurm, with this client library.

## 0. Prerequisite: OMERO, Slurm and `biomero`.

We assume you have these 3 components setup and connected. If not, follow the main [README](README.md) first.

## 1. Grab the data and pipeline

We want to try something ready-made, and we like spots here at the AMC. 

So let's grab this spot-counting example from the cellprofiler website:
https://github.com/tischi/cellprofiler-practical-NeuBIAS-Lisbon-2017/blob/master/practical-handout.md

## 2. Try the pipeline locally

### UI
It is always a good idea to test your algorithms locally before jumping to remote compute. 
You can walk through the readme, or open the [PLA-dot-counting-with-speckle-enhancement.cpproj](https://github.com/tischi/cellprofiler-practical-NeuBIAS-Lisbon-2017/blob/master/PLA-dot-counting-with-speckle-enhancement.cpproj). It seems to be a bit older, so we have to fix the threshold (`(0.0` to `0.0`) and change the input to our local file location.

### Export pipeline file only
Cellprofiler works with both `.cpproj` and `.cppipe`. The project version hardcodes the filepaths in there, which we don't want. So go to `File` > `Export` > `Pipeline` and save this as a `.cppipe` file.

Another bonus is that `.cppipe` is human-readable and editable. Important later on.

### headless
After it works, let's try it headless too:
```bash
./cellprofiler.exe -c -p '<path-to>\PLA-dot-counting-with-speckle-enhancement.cppipe' -o '<path-to>\cellprofiler_results' -i '<path-to>\PLA_data
```

Here we provide the input images (`-i`), the output folder (`-o`), the project (`-p`) and headless mode (`-c`).

See [this blog](https://carpenter-singh-lab.broadinstitute.org/blog/getting-started-using-cellprofiler-command-line) for more info on the commandline parameters.

## 3. Upload the data to OMERO

Let's make a screen out of these 8 wells, for fun.

- Open the importer.
- Since there is no screen metadata in the files, first create a project and dataset in OMERO. 
- Import the PLA_data folder there.
- Go to the Web UI.
- Select the new dataset.
- Activate script `Dataset to Plate` (under omero/util_scripts/).
    - Fill in 8 wells per row (optional)
    - Screen: PLA_data
- Now we have a plate with 8 wells in a screen in OMERO.

## 4. Package the cellprofiler in a FAIR package

To create a FAIR workflow, let's follow the steps from Biaflows for creating a new workflow, as they explained it quite well already: https://neubias-wg5.github.io/creating_bia_workflow_and_adding_to_biaflows_instance.html

We just ignore some parts specific to the BIAFLOWS server, like adding as a trusted source. We will add the workflow to OMERO and Slurm instead, as a final step.

### 0. Create a workflow Github repository
To kickstart, we can reuse some of the workflow setup for CellProfiler from [Neubias](https://github.com/Neubias-WG5/W_NucleiSegmentation-CellProfiler) github.

You can follow along, or just use my version at the end (https://github.com/TorecLuik/W_SpotCounting-CellProfiler)

- Login/create an account on Github
- Go to link above.
- Go to `Use this template`
    - `Create a new repository`
        - Name it `W_SpotCounting-CellProfiler`
        - Keep it Public
- Clone your new repository locally
    - `Code` > `Clone` > `HTTPS` > Copy
    - `git clone https://github.com/<...>/W_SpotCounting-CellProfiler.git`
- Open the folder in your favorite [editor](https://code.visualstudio.com/)
- Copy the project we want to this folder e.g. `PLA-dot-counting-with-speckle-enhancement.cpproj`

### a. Create a Dockerfile for cellprofiler

The Dockerfile installs our whole environment.

We want:

1. Cellprofiler
2. Cytomine/Biaflows helper libraries (for Input/Output)
3. Our workflow files:
  - `wrapper.py` (the logic to run our workflow)
  - `descriptor.json` (the metadata of our workflow)
  - `*.cppipe` (our cellprofiler pipeline)

Now it turns out that this [Dockerfile](https://github.com/Neubias-WG5/W_NucleiSegmentation-CellProfiler) uses an old version of CellProfiler (with Python 2).
We want the newest one, so I rewrote the Dockerfile: 

<details>
  <summary>Our new/changed Dockerfile</summary>

```Dockerfile
FROM cellprofiler/cellprofiler
```
Instead of installing cellprofiler manually, it turns out they host containers images themselves, so let's reuse [those](https://hub.docker.com/r/cellprofiler/cellprofiler).

```Dockerfile
# Install Python3.7
RUN apt-get update && apt-get install -y python3.7 python3.7-dev python3.7-venv
RUN python3.7 -m pip install --upgrade pip && python3.7 -m pip install Cython
```
This cellprofiler image is quite modern, but we need an older Python to work with the Cytomine/Biaflows libraries. So we Install Python3.7 (and Cython package).
```Dockerfile
# ------------------------------------------------------------------------------
# Install Cytomine python client
RUN git clone https://github.com/cytomine-uliege/Cytomine-python-client.git && \
    cd Cytomine-python-client && git checkout tags/v2.7.3 && \ 
    python3.7 -m pip install . && \
    cd .. && \
    rm -r Cytomine-python-client

# ------------------------------------------------------------------------------
# Install BIAFLOWS utilities (annotation exporter, compute metrics, helpers,...)
RUN apt-get update && apt-get install libgeos-dev -y && apt-get clean
RUN git clone https://github.com/Neubias-WG5/biaflows-utilities.git && \
    cd biaflows-utilities/ && git checkout tags/v0.9.1 && python3.7 -m pip install .

# install utilities binaries
RUN chmod +x biaflows-utilities/bin/*
RUN cp biaflows-utilities/bin/* /usr/bin/ && \
    rm -r biaflows-utilities
```

These 2 parts install specific versions of the biaflows library and Cytomine library with Python 3.7.

```Dockerfile
# ------------------------------------------------------------------------------
# Add repository files: wrapper, command and descriptor
RUN mkdir /app
ADD wrapper.py /app/wrapper.py
ADD PLA-dot-counting-with-speckle-enhancement.cppipe /app/PLA-dot-counting-with-speckle-enhancement.cppipe
ADD descriptor.json /app/descriptor.json

ENTRYPOINT ["python3.7","/app/wrapper.py"]

```

Finally we add our own workflow to `/app` folder:
- `wrapper.py`
- `.cppipe`
- `descriptor.json` 

And we tell the image to call `wrapper.py` with python3.7 when we start it up using an `ENTRYPOINT`. This also forwards commandline parameters that you provide to the `wrapper.py` script, e.g. workflow parameters.

</details>

### b. Setup the metadata in `descriptor.json`

We actually don't have any input parameters (except the default input/output) at this moment. Look at [this](#extra-how-to-add-workflow-parameters-to-cellprofiler) extra chapter for more info on how to approach that.

So we can just use the basic `descriptor.json` that was given and remove the last 2 non-cytomine parameters.
Mainly, update the name, description and where we will publish the container (your new dockerhub account).

<details>
  <summary>Example full json</summary>

```json
{
  "name": "SpotCounting-CellProfiler",
  "description": "Workflow for spot counting in CellProfiler",
  "container-image": {
    "image": "torecluik/w_spotcounting-cellprofiler",
    "type": "singularity"
  },
  "command-line": "python wrapper.py CYTOMINE_HOST CYTOMINE_PUBLIC_KEY CYTOMINE_PRIVATE_KEY CYTOMINE_ID_PROJECT CYTOMINE_ID_SOFTWARE",
  "inputs": [
    {
      "id": "cytomine_host",
      "value-key": "@ID",
      "command-line-flag": "--@id",
      "name": "BIAFLOWS host",
      "set-by-server": true,
      "optional": false,
      "type": "String"
    },
    {
      "id": "cytomine_public_key",
      "value-key": "@ID",
      "command-line-flag": "--@id",
      "name": "BIAFLOWS public key",
      "set-by-server": true,
      "optional": false,
      "type": "String"
    },
    {
      "id": "cytomine_private_key",
      "value-key": "@ID",
      "command-line-flag": "--@id",
      "name": "BIAFLOWS private key",
      "set-by-server": true,
      "optional": false,
      "type": "String"
    },
    {
      "id": "cytomine_id_project",
      "value-key": "@ID",
      "command-line-flag": "--@id",
      "name": "BIAFLOWS project ID",
      "set-by-server": true,
      "optional": false,
      "type": "Number"
    },
    {
      "id": "cytomine_id_software",
      "value-key": "@ID",
      "command-line-flag": "--@id",
      "name": "BIAFLOWS software ID",
      "set-by-server": true,
      "optional": false,
      "type": "Number"
    }
  ],

  "schema-version": "cytomine-0.1"
}
```
</details>


### c. Update the command in `wrapper.py`

So the wrapper gets called when the container starts. 
This is where we 'wrap' our pipeline by handling input/output and parameters.
We also have to make sure that we call the pipeline correctly here.

<details>
  <summary>Our changes to the wrapper</summary>

This first part we keep the same: the `BiaflowsJob` will parse the commandline parameters for us and provide those as `bj.parameter.<param_name>` if we did want them. But we don't use any right now.
```python
def main(argv):
    base_path = "{}".format(os.getenv("HOME")) # Mandatory for Singularity
    problem_cls = CLASS_OBJSEG

    with BiaflowsJob.from_cli(argv) as bj:
        bj.job.update(status=Job.RUNNING, progress=0, statusComment="Initialisation...")
        # 1. Prepare data for workflow
        in_imgs, gt_imgs, in_path, gt_path, out_path, tmp_path = prepare_data(problem_cls, bj, is_2d=True, **bj.flags)
```
The second part (where we call our pipeline) we can simplify a bit, as we don't need to parse parameters for cellprofiler. See [later](#extra-how-to-add-workflow-parameters-to-cellprofiler) for how to start handling that.

We specifically name the cppipe that we added to `/app`, and we use `subprocess.run(...)` to execute our cellprofiler headless on the commandline: `cellprofiler -c -r -p ... -i ... -o ... -t`. 

In theory we could also use the cellprofiler python package here, for more control. But in general, we can run any commandline program with `subprocess.run`, so this wrapper will look similar for most workflows.
```python
        pipeline = "/app/PLA-dot-counting-with-speckle-enhancement.cppipe"

        # 2. Run CellProfiler pipeline
        bj.job.update(progress=25, statusComment="Launching workflow...")
        
        ## If we want to allow parameters, we have to parse them into the pipeline here
        # mod_pipeline = parse_cellprofiler_parameters(bj, pipeline, tmp_path)
        mod_pipeline = pipeline

        shArgs = [
            "cellprofiler", "-c", "-r", "-p", mod_pipeline,
            "-i", in_path, "-o", out_path, "-t", tmp_path,
        ]
        status = run(" ".join(shArgs), shell=True)
```

Finally, we don't change much to the rest of the script and just handle the return code. 0 means success, so then we just log to the logfile. 

There is some built-in logic for `Biaflows`, like uploading results and metrics.
We keep it in for the logs, but they are essentially a [`no-op`](https://en.wikipedia.org/wiki/NOP_(code)) because we will provide the command-line parameters `--local` and `-nmc` (`n`o `m`etric `c`omputation).

</details>

Full changes can be found [here](https://github.com/TorecLuik/W_SpotCounting-CellProfiler/blob/master/wrapper.py)


### d. Run locally

Now that we have a docker, we can run this locally or anywhere that we have docker installed, without the need for having the right version of cellprofiler, etc.
Let's try it out:

0. Setup your data folder like this: 
- `PLA` as main folder
  - `PLA_data` with the 8 images, as subfolder
  - `out` as empty subfolder
  - `gt` as empty subfolder
1. Build a container: `docker build -t spotcounting-cp .` (Note the `.` is important, it means `this folder`)
2. Run the container on the `PLA` folder like this: `docker run --rm -v <my-drive>\PLA\:/data-it spotcounting-cp --local --infolder /data-it/PLA_data --outfolder /data-it/out --gtfolder /data-it/gt  -nmc `

This should work the same as [before](#2-try-the-pipeline-locally), with a bit of extra logging thrown in. 
Except now, we didn't need to have cellprofiler installed! Anyone with `Docker` (or `Podman` or `Singularity`) can run this workflow now.


### e. Publish to GitHub and DockerHub

So how do other people get to use our workflow? 

1. We publish the source online on Github:

- Commit to git: `git commit -m 'Update with spotcounting pipeline' -a`
- Push to github: `git push`
- Setup automated release to Dockerhub:
  - First, create a free account on Dockerhub if you don't have one
  - On Dockerhub, login and create a new `Access Token` via `Account Settings` / `Security`. Name it `Github` or something. Copy this token (to a file).
  - Back on your Github repository, add 2 secrets by going to `Settings` / `Secrets and variables` / `Actions` / `New repository secret`
    - First, add Name: `DOCKERHUB_USERNAME` and Secret: `<your-dockerhub-username>`
    - Also, add Name: `DOCKERHUB_TOKEN` and Secret: `<token-that-you-copied>` 
- Now, tag and release this as a new version on Github (and automatically Dockerhub): 
  - Pretty easy to do from Github page: `Releases` > `new release`. 
  - Add a tag like `v1.0.0`. 
  - Now, the Github Action `Docker Image CI` will build the container for you and publish it on Dockerhub via the credentials you provided. This will take a few minutes, you can follow along at the `Actions` tab.
  - Now you can verify that it is available online:
https://hub.docker.com/u/your-dockerhub-user
- Great! now everybody (with internet access) can pull your workflow image and run it [locally](#d-run-locally): `docker run --rm -v <my-drive>\PLA\:/data-it <your-dockerhub-user>/w_spotcounting-cellprofiler:v1.0.0 --local --infolder /data-it/PLA_data --outfolder /data-it/out --gtfolder /data-it/gt -nmc`

And this is what we will make OMERO do on the Slurm cluster next.


#### Optional: Manually publish the image on Dockerhub:

- First, create an account on Dockerhub if you don't have one
- Login locally on the commandline to this account too: `docker login` 
- (Optional) Build your latest docker image if you didn't do that yet (`docker build -t spotcounting-cp .`). 
- Tag your local Docker image with a new tag to match this Dockerhub account and release: `docker tag spotcounting-cp:latest <your-dockerhub-user>/w_spotcounting-cellprofiler:v1.0.0`
- Push your tagged image to Dockerhub: `docker push <your-dockerhub-user>/w_spotcounting-cellprofiler:v1.0.0`
- Now you can verify that it is available online:
https://hub.docker.com/u/your-dockerhub-user

E.g. mine can be found @ https://hub.docker.com/r/torecluik/w_spotcounting-cellprofiler/tags 

- Great! now everybody (with internet access) can pull your workflow image and run it [locally](#d-run-locally): `docker run --rm -v <my-drive>\PLA\:/data-it <your-dockerhub-user>/w_spotcounting-cellprofiler:v1.0.0 --local --infolder /data-it/PLA_data --outfolder /data-it/out --gtfolder /data-it/gt -nmc`

And this is what we will make OMERO do on the Slurm cluster next.

## 5. Add this workflow to the OMERO Slurm Client

1. Let's adjust the `slurm-config.ini` on our OMERO processor server.

In the `[MODEL]` section we add our new workflow:
```ini
# -------------------------------------
# CELLPROFILER SPOT COUNTING
# -------------------------------------
# The path to store the container on the slurm_images_path
cellprofiler_spot=cellprofiler_spot
# The (e.g. github) repository with the descriptor.json file
cellprofiler_spot_repo=https://github.com/TorecLuik/W_SpotCounting-CellProfiler/tree/v1.0.0
# The jobscript in the 'slurm_script_repo'
cellprofiler_spot_job=jobs/cellprofiler_spot.sh
```
Note that we link to the `v1.0.0` specifically.

When using a new version, like `v1.0.1`, update this config again.
For example, I had a bugfix, so I released [my workflow](https://hub.docker.com/r/torecluik/w_spotcounting-cellprofiler/tags) to `v1.0.1`, using the release + push + update steps.

For me, updating is done by rebuilding my docker container for the processor worker:
`docker-compose up -d --build omeroworker-5`

2. and recreate the Slurm environment:

- Run `SlurmClient.from_config(init_slurm=true)` on the OMERO processor server.

<details>
  <summary>E.g. using this omero script</summary>
  
```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Original work Copyright (C) 2014 University of Dundee
#                                   & Open Microscopy Environment.
#                    All Rights Reserved.
# Modified work Copyright 2022 Torec Luik, Amsterdam UMC
# Use is subject to license terms supplied in LICENSE.txt
#
# Example OMERO.script to instantiate a 'empty' Slurm connection.

import omero
import omero.gateway
from omero import scripts
from omero.rtypes import rstring, unwrap
from biomero import SlurmClient
import logging

logger = logging.getLogger(__name__)


def runScript():
    """
    The main entry point of the script
    """

    client = scripts.client(
        'Slurm Init',
        '''Will initiate the Slurm environment for workflow execution.

        You can provide a config file location, 
        and/or it will look for default locations:
        /etc/slurm-config.ini
        ~/slurm-config.ini
        ''',
        scripts.Bool("Init Slurm", grouping="01", default=True),
        scripts.String("Config file", optional=True, grouping="01.1",
                       description="The path to your configuration file. Optional."),
        namespaces=[omero.constants.namespaces.NSDYNAMIC],
    )

    try:
        message = ""
        init_slurm = unwrap(client.getInput("Init Slurm"))
        if init_slurm:
            configfile = unwrap(client.getInput("Config file"))
            if not configfile:
                configfile = ''
            with SlurmClient.from_config(configfile=configfile,
                                         init_slurm=True) as slurmClient:
                slurmClient.validate(validate_slurm_setup=True)
                message = "Slurm is setup:"
                models, data = slurmClient.get_all_image_versions_and_data_files()
                message += f"Models: {models}\nData:{data}"

        client.setOutput("Message", rstring(str(message)))

    finally:
        client.closeSession()


if __name__ == '__main__':
    runScript()

```
</details>

Now your Slurm cluster has 
- your image 'v1.0.0'.
- And also a job-script for Slurm, automatically generated (unless you changed that behaviour in the `slurm-config`).

## 6. Add a OMERO script to run this from the Web UI

1. select a screen / dataset
2. select workflow
3. run workflow!
4. check progress
5. Import resulting data

I have created several OMERO scripts using this library, and the [`run_workflow`]() can do this for us.
It will attach the results as a zipfile attachment to the screen.
Perhaps we can integrate with OMERO.Tables in the future.

## Extra: How to add workflow parameters to cellprofiler?

So normally, adding workflow parameters to your commandline in `wrapper.py` is easy, like [this](https://github.com/Neubias-WG5/W_NucleiSegmentation-Cellpose/blob/master/wrapper.py#L62C8-L65C37):
```python
        # Add here the code for running the analysis script
        #"--chan", "{:d}".format(nuc_channel)
        cmd = ["python", "-m", "cellpose", "--dir", tmp_path, "--pretrained_model", "nuclei", "--save_tif", "--no_npy", "--chan", "{:d}".format(nuc_channel), "--diameter", "{:f}".format(bj.parameters.diameter), "--cellprob_threshold", "{:f}".format(bj.parameters.prob_threshold)]
        status = subprocess.run(cmd)
```
Here we add `bj.parameters.diameter` (described [here](https://github.com/Neubias-WG5/W_NucleiSegmentation-Cellpose/blob/master/descriptor.json#L54C6-L65C7)) as `"--diameter", "{:f}".format(bj.parameters.diameter)`.

However, cellprofiler does not support changing pipeline parameters from the commandline. Maybe it will in the future. 
For now, we have 3 options:

1. Edit the `.cppipe` file and override our parameters there automatically
2. Use the Python `cellprofiler` library in `wrapper.py` and open and edit the `pipeline`.
2. Add an extra python script that does number 2, which we call from the `wrapper.py` and which does accept commandline arguments. 

For 1., this is where `parseCPparam` [function](https://github.com/Neubias-WG5/W_NucleiSegmentation-CellProfiler/blob/master/wrapper.py#L8C1-L26C1) comes in (in `wrapper.py`). I have updated it a bit in [my version](https://github.com/TorecLuik/W_SpotCounting-CellProfiler/blob/master/wrapper.py#L10C1-L51C24).
It matches the `name` in `descriptor.json` literally with the same string in `.cppipe`, and then changes the values to the new ones provided on the commandline.
However, if you use the same module twice (like in our example pipeline), it will overwrite both of them with the same value.
In our example, that does not work properly, e.g. the size of a nucleus should NOT be the same as the size of a spot.

Options 2 and 3 are an exercise for the reader. There is an example in the OMERO docs of using the CellProfiler Python API: [Getting started with CellProfiler and OMERO](https://omero-guides.readthedocs.io/en/latest/cellprofiler/docs/gettingstarted.html).

## Extra 2: We should add a LICENSE

See the importance of a license [here](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository#choosing-the-right-license): 

> You're under no obligation to choose a license. However, without a license, the default copyright laws apply, meaning that you retain all rights to your source code and no one may reproduce, distribute, or create derivative works from your work. If you're creating an open source project, we strongly encourage you to include an open source license.

So, we are essentially not allowed to make all these changes and use their template without a license. We will just assume we have a license as they explain all these steps in their docs.
To make this easier for the future, always add a license. I [asked](https://github.com/Neubias-WG5/W_NucleiSegmentation-CellProfiler/issues/2) them to add one to the example workflows.

A nice permissive default is [Apache 2.0](https://choosealicense.com/licenses/apache-2.0/). It allows people to generally use it however they want, private / commercial / open / closed etc.

But there is also `copyleft`, where people can only adapt your code if they also keep the same license on all their code; e.g. [GNU](https://choosealicense.com/licenses/gpl-3.0/). That is a bit more restrictive.



