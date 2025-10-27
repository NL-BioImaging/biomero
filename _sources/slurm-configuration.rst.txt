SLURM Configuration Guide
========================

This guide covers configuring BIOMERO for High-Performance Computing (HPC) clusters using SLURM.

Configuration File Overview
---------------------------

BIOMERO uses a ``slurm-config.ini`` file to configure connection and execution parameters for SLURM clusters. The configuration file contains several sections:

* **SSH**: Connection settings for the SLURM cluster
* **SLURM**: Paths and execution settings
* **CONVERTERS**: File format conversion settings  
* **MODELS**: Workflow and container definitions

Basic Configuration
-------------------

SSH Section
~~~~~~~~~~~

Configure the SSH connection to your SLURM cluster:

.. code-block:: ini

   [SSH]
   # The alias for the SLURM SSH connection
   host=slurm
   # Configure additional SSH settings in your SSH config file

SLURM Paths Section
~~~~~~~~~~~~~~~~~~~

Configure paths on the SLURM cluster:

.. code-block:: ini

   [SLURM]
   # Data storage path (relative to user home or absolute)
   slurm_data_path=my-scratch/data
   
   # Container images path
   slurm_images_path=my-scratch/singularity_images/workflows
   
   # Converter images path  
   slurm_converters_path=my-scratch/singularity_images/converters
   
   # Job scripts path
   slurm_script_path=my-scratch/slurm-scripts

Environment Variables and Container Binding
-------------------------------------------

Singularity/Apptainer Bind Paths
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some HPC environments require explicit directory binding for containers. If your HPC administrator instructs you to set the ``APPTAINER_BINDPATH`` environment variable, configure:

.. code-block:: ini

   [SLURM]
   # Path to bind to containers via APPTAINER_BINDPATH
   # Required when default data folder is not bound to container
   slurm_data_bind_path=/path/to/your/data

**When to use this setting:**

* Your containers cannot access data files
* HPC documentation mentions setting ``APPTAINER_BINDPATH`` 
* You receive "file not found" errors during workflow execution
* Your system administrator recommends explicit path binding

**Leave empty (default) when:**

* Your HPC automatically binds common directories like ``/home``, ``/tmp``
* Containers can access data without issues
* No explicit binding configuration is required

Partition Configuration
~~~~~~~~~~~~~~~~~~~~~~~

Specify a SLURM partition for conversion jobs:

.. code-block:: ini

   [SLURM]  
   # Partition for conversion jobs (optional)
   slurm_conversion_partition=cpu-short

Leave empty to use the system default partition.

Workflow Configuration
----------------------

The ``[MODELS]`` section defines available workflows:

.. code-block:: ini

   [MODELS]
   # Workflow name and settings
   cellpose=cellpose
   cellpose_repo=https://github.com/example/W_NucleiSegmentation-Cellpose/tree/v1.4.0
   cellpose_job=jobs/cellpose.sh
   
   # Override job parameters (optional)
   cellpose_job_mem=4GB
   cellpose_job_gres=gpu:1g.10gb:1

Troubleshooting
===============

Common Issues and Solutions
---------------------------

Container Access Errors
~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Workflows fail with "file not found" or permission errors.

**Solutions:**

1. **Check bind paths:** Configure ``slurm_data_bind_path`` if required
2. **Verify permissions:** Ensure SLURM user can access data directories  
3. **Check container binding:** Verify Singularity/Apptainer can access required paths

.. code-block:: ini

   # Add explicit binding if needed
   slurm_data_bind_path=/data/your-scratch/data

SSH Connection Issues  
~~~~~~~~~~~~~~~~~~~~~

**Problem:** Cannot connect to SLURM cluster.

**Solutions:**

1. **SSH config:** Verify SSH configuration for the host alias
2. **Authentication:** Check SSH keys and authentication methods
3. **Network:** Confirm network connectivity to the cluster

.. code-block:: bash

   # Test SSH connection manually
   ssh your-slurm-host
   
   # Check SSH config
   ssh -F ~/.ssh/config your-slurm-host

Job Submission Failures
~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Jobs fail to submit or execute.

**Solutions:**

1. **Partition access:** Check if specified partition is available
2. **Resource limits:** Verify memory/CPU/GPU requests are within limits  
3. **Queue policies:** Check SLURM queue policies and restrictions

.. code-block:: ini

   # Use appropriate partition
   cellpose_job_partition=gpu-partition
   
   # Adjust resource requests
   cellpose_job_mem=8GB
   cellpose_job_gres=gpu:1

Path Configuration Issues
~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Containers or scripts not found.

**Solutions:**

1. **Absolute vs relative paths:** Use appropriate path format for your setup
2. **Directory existence:** Verify directories exist on SLURM cluster
3. **Path permissions:** Check read/write permissions

.. code-block:: ini

   # Relative to home directory
   slurm_data_path=my-scratch/data
   
   # Or absolute path  
   slurm_data_path=/data/users/username/my-scratch/data

FAQ
===

**Q: Should I use relative or absolute paths?**

A: Use relative paths if your SLURM setup expects paths relative to the user home directory. Use absolute paths if you need to specify exact filesystem locations.

**Q: When do I need to set slurm_data_bind_path?**

A: Set this when your HPC administrator tells you to configure ``APPTAINER_BINDPATH``, or when containers cannot access your data directories.

**Q: How do I know which partition to use?**

A: Check with your HPC documentation or administrator. Common partitions include ``cpu``, ``gpu``, ``short``, ``long``. Leave empty to use the default.

**Q: Can I override job parameters for specific workflows?**

A: Yes, add ``workflowname_job_parameter=value`` entries in the ``[MODELS]`` section to override default SLURM job parameters.

**Q: How do I debug workflow execution issues?**

A: Check SLURM job logs, verify container access to data, and ensure all required directories exist with proper permissions.

Further Reading
===============

* `SLURM Documentation <https://slurm.schedmd.com/documentation.html>`_
* `Singularity User Guide <https://docs.sylabs.io/guides/latest/user-guide/>`_
* `Apptainer Documentation <https://apptainer.org/docs/>`_