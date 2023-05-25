# omero_slurm_client
The Omero-Slurm Python package is a library that facilitates working with a Slurm cluster in the context of the Omero platform. The package includes the SlurmClient class, which extends the Fabric library's Connection class to provide SSH-based connectivity and interaction with a Slurm cluster. The package enables users to submit jobs, monitor job status, retrieve job output, and perform other Slurm-related tasks. Additionally, the package offers functionality for configuring and managing paths to Slurm data and Singularity images, as well as specific image models and their associated repositories. Overall, the Omero-Slurm package simplifies the integration of Slurm functionality within the Omero platform and provides an efficient workflow for working with Slurm clusters.

# SlurmClient
The SlurmClient class is a Python class that extends the Connection class from the Fabric library. It allows connecting to and interacting with a Slurm cluster over SSH. 

It includes attributes for specifying paths to directories for Slurm data and Singularity images, as well as specific paths, repositories, and Dockerhub information for different Singularity image models. The class provides methods for running commands on the remote Slurm host, submitting jobs, checking job status, retrieving job output, and tailing log files. It also offers a from_config class method to create a SlurmClient object by reading configuration parameters from a file. Overall, the class provides a convenient way to work with Slurm clusters and manage job execution and monitoring.

# slurm-config.ini
The slurm-config.ini file is a configuration file used by the Omero-Slurm Python package to specify various settings related to SSH and Slurm. Here is a brief description of its contents:

[SSH]: This section contains SSH settings, including the alias for the SLURM SSH connection (host). Additional SSH configuration can be specified in the user's SSH config file or in /etc/fabric.yml.

[SLURM]: This section includes settings specific to Slurm. It defines the paths on the SLURM entrypoint for storing data files (slurm_data_path), container image files (slurm_images_path), and Slurm job scripts (slurm_script_path). It also specifies the repository (slurm_script_repo) from which to pull the Slurm scripts.

[MODELS]: This section is used to define different model settings. Each model has a unique key and requires corresponding values for <key>_repo (repository containing the descriptor.json file), <key>_image (image location, such as Dockerhub), and <key>_job (jobscript in the slurm_script_repo). The example shows settings for several segmentation models, including Cellpose, Stardist, CellProfiler, DeepCell, and ImageJ.

The slurm-config.ini file allows users to configure paths, repositories, and other settings specific to their Slurm cluster and the Omero-Slurm package, providing flexibility and customization options.

# Setup your SSH connection to Slurm
To connect an Omero processor to a Slurm cluster using the Omero-Slurm library, users can follow these steps. First, ensure that the `slurm-config.ini` file is correctly configured with the necessary SSH and Slurm settings, including the host, data path, images path, and model details. Customize the configuration according to the specific Slurm cluster setup. Next, create an SSH config file named `config` in the `.ssh` directory within the user's home directory (`~/.ssh/config`). This file should specify the hostname, username, port, and private key path for the Slurm cluster. With the configuration files in place, users can utilize the `SlurmClient` class from the Omero-Slurm library to connect to the Slurm cluster over SSH, enabling the submission and management of Slurm jobs from an Omero processor.

Examples of these files are given in the [resources](resources) folder.