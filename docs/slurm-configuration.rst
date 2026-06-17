SLURM Configuration Guide
========================

This guide covers how the BIOMERO Python client reads ``slurm-config.ini``,
which settings affect runtime behaviour, and when environment variables should
override the file.

For a full lookup table of all supported configuration environment variables and
their ``slurm-config.ini`` counterparts, see :doc:`configuration-reference`.

How BIOMERO Finds Configuration
-------------------------------

``SlurmClient.from_config()`` reads configuration from these locations, in this order:

1. ``/etc/slurm-config.ini``
2. ``/OMERO/slurm-config.ini``
3. ``~/slurm-config.ini``
4. the explicit ``configfile=...`` path passed by the caller

Later files override earlier files.

For the settings documented on this page, environment variables then override
the resolved ``slurm-config.ini`` value when supported.

Configuration Sections
----------------------

BIOMERO uses these sections in ``slurm-config.ini``:

* ``[SSH]`` for the Fabric SSH host alias
* ``[SLURM]`` for shared paths and client-level runtime behaviour
* ``[CONVERTERS]`` for external converter container images
* ``[MODELS]`` for workflow repositories, job scripts, and per-workflow sbatch overrides

Minimal Working Example
-----------------------

.. code-block:: ini

   [SSH]
   host=slurm

   [SLURM]
   slurm_data_path=my-scratch/data
   slurm_images_path=my-scratch/singularity_images/workflows
   slurm_converters_path=my-scratch/singularity_images/converters
   slurm_script_path=my-scratch/slurm-scripts

   [MODELS]
   cellpose=cellpose
   cellpose_repo=https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose/tree/v1.2.7
   cellpose_job=jobs/cellpose.sh

Core Path Settings
------------------

These ``[SLURM]`` settings define where BIOMERO stores and looks for runtime data:

.. list-table:: Core paths
   :header-rows: 1

   * - Setting
     - Purpose
     - Runtime impact
   * - ``slurm_data_path``
     - Base directory for transferred input data and produced output data
     - Used to build ``DATA_PATH`` for workflow and conversion jobs
   * - ``slurm_images_path``
     - Base directory for workflow containers on the HPC
     - Used to build ``IMAGE_PATH`` and locate workflow ``.sif`` files
   * - ``slurm_converters_path``
     - Base directory for converter containers on the HPC
     - Used for conversion workflows and converter setup
   * - ``slurm_script_path``
     - Directory containing generated or cloned Slurm job scripts
     - Used when submitting workflow jobs and conversion jobs

Relative paths are typically interpreted relative to the remote user's home directory.
Absolute paths are also supported if that better matches your cluster.

Operational Settings
--------------------

Container bind path
~~~~~~~~~~~~~~~~~~~

Use ``slurm_data_bind_path`` when your Apptainer or Singularity jobs need an
explicit ``APPTAINER_BINDPATH`` so containers can see the transferred data.

Impact:

* If set, BIOMERO injects ``APPTAINER_BINDPATH`` into workflow and conversion job environments.
* If unset, BIOMERO relies on the cluster's default container bind behaviour.

Use this when:

* your HPC administrator told you to set ``APPTAINER_BINDPATH``
* containers cannot see files under ``slurm_data_path``
* jobs fail with missing input or missing output path errors inside the container

Conversion partition
~~~~~~~~~~~~~~~~~~~~

Use ``slurm_conversion_partition`` when conversion jobs need an explicit partition.

Impact:

* If set, BIOMERO adds ``CONVERSION_PARTITION`` to the conversion environment.
* The conversion submission path then passes that partition information through the job script.
* If unset, conversion jobs use the cluster default partitioning behaviour.

Sacct history window
~~~~~~~~~~~~~~~~~~~~

The client uses ``sacct`` to list historical jobs. Two settings control how far
back it looks:

* ``sacct_start_time``: absolute date, format ``YYYY-MM-DD``
* ``sacct_days_ago``: relative rolling window in days

Effective precedence when BIOMERO chooses a default start time:

1. built-in fallback: ``2023-01-01``
2. resolved ``sacct_start_time``
3. resolved ``sacct_days_ago``

That means ``sacct_days_ago`` wins over ``sacct_start_time`` when both are set.

Environment-based overrides follow the same logic:

* ``BIOMERO_SACCT_START_TIME`` overrides the ini absolute date
* ``BIOMERO_SACCT_START_DAYS_AGO`` overrides both the ini rolling window and the absolute date when valid
* if ``BIOMERO_SACCT_START_DAYS_AGO`` is invalid, BIOMERO falls back to the resolved absolute date instead of failing hard

Processing settings
~~~~~~~~~~~~~~~~~~~

``env_file_submission``

Impact:

* When ``false``: BIOMERO submits jobs by passing environment variables directly through the SSH/Fabric execution path.
* When ``true``: BIOMERO writes a per-job shell file with exports and passes that file as ``$1`` to the job script.
* Generated job scripts also receive a small sourcing block so they can read that env file.

Use this when:

* SSH session environment propagation into ``sbatch`` is unreliable on your cluster
* ``AcceptEnv`` or related SSH policy prevents the expected workflow parameters from arriving in jobs

``inject_gpu_flag``

Impact:

* When ``false``: generated job scripts keep their normal GPU handling.
* When ``true``: BIOMERO uses a ``GPU_FLAG`` mechanism so generated scripts can switch between CPU and GPU execution at submission time.
* This only affects generated scripts and workflow submission logic that uses the ``use_gpu`` parameter.

Use this when:

* you want one generated job script to run in both CPU and GPU modes
* your generated scripts contain the standard singularity/apptainer GPU flag pattern

``gpu_resource_flag``

Impact:

* Controls which sbatch GPU resource flag BIOMERO appends for shared GPU defaults.
* Typical values are ``gres`` and ``gpus``.
* This affects only BIOMERO-added fallback GPU params; explicit per-workflow job params still win.

Use this when:

* your cluster expects ``--gpus=...`` instead of ``--gres=...``
* you want shared GPU fallback injection to match local scheduler conventions

Image pull execution mode
~~~~~~~~~~~~~~~~~~~~~~~~~

``slurm_image_pull_via_sbatch``

Impact:

* When ``false``: BIOMERO starts image pulls/builds via the existing remote shell path.
* When ``true``: BIOMERO submits image pulls/builds through ``sbatch`` instead.
* This applies to both workflow image setup and configured converter image setup.

Use this when:

* container pulls/builds are too heavy for the login node
* your cluster requires build work to run as scheduled jobs

``image_pull_cpus`` and ``image_pull_mem``

Impact:

* Control the resources requested when ``slurm_image_pull_via_sbatch=true``.
* These values are passed to the pull/build submission jobs for workflow and converter images.

Apptainer temp and cache directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``apptainer_tmpdir`` and ``apptainer_cachedir`` when the default temporary
or cache location is too small for large image pulls/builds.

Impact:

* If set, BIOMERO exports the corresponding Apptainer/Singularity env vars into pull/build commands.
* The same settings apply to both image setup modes: direct and sbatch-based.
* If unset, BIOMERO leaves Apptainer/Singularity at the cluster defaults.

Default GPU fallback settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``gpu_partition`` and ``gpu_gres`` are shared client defaults, not per-workflow settings.

Impact:

* They are only considered when ``inject_gpu_flag`` is enabled and the workflow is submitted with ``use_gpu=true``.
* If a workflow already has per-workflow sbatch overrides such as ``cellpose_job_partition`` or ``cellpose_job_gres``, those per-workflow settings take precedence.
* If no per-workflow ``--partition`` or ``--gres`` is already present, BIOMERO appends these defaults to the submission command.

This gives a clear precedence order for GPU resource requests:

1. explicit per-workflow ``[MODELS]`` sbatch overrides
2. shared ``gpu_partition`` and ``gpu_gres`` defaults
3. no GPU partition or gres added by BIOMERO

ZIP command
~~~~~~~~~~~

``slurm_zip_cmd`` controls which archive command BIOMERO uses when it zips job output on the cluster.

Impact:

* If unset, BIOMERO defaults to ``$(command -v 7z || command -v 7za)``.
* If set, BIOMERO uses the configured command directly when generating the zip command.

Use this when:

* your cluster exposes only ``7za`` or only ``7z``
* auto-detection is not reliable in your environment

Workflow And Model Configuration
--------------------------------

For most installations, we recommend leaving ``slurm_script_repo`` empty and
using BIOMERO's generated job scripts.

Why this is the safer default:

* generated scripts are updated together with BIOMERO and can be redeployed via ``init_slurm=True`` or script regeneration
* we do not maintain a separate always-up-to-date public scripts repository for operators to track
* an external scripts repository can drift from the BIOMERO version you are running and break newer features or assumptions between minor releases
* adding or evolving workflows is usually simpler through descriptor-driven generation than through maintaining a custom scripts repository

Use ``slurm_script_repo`` only when you explicitly need custom hand-maintained
job scripts and are prepared to keep them aligned with the BIOMERO version in
production.

The ``[MODELS]`` section defines each workflow and optionally adds per-workflow
sbatch overrides.

Example:

.. code-block:: ini

   [MODELS]
   cellpose=cellpose
   cellpose_repo=https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose/tree/v1.2.7
   cellpose_job=jobs/cellpose.sh
   cellpose_job_gres=gpu:1g.10gb:1
   cellpose_job_partition=gpu
   cellpose_job_mem=16GB

How BIOMERO interprets these keys:

* ``cellpose`` defines the subdirectory under ``slurm_images_path``
* ``cellpose_repo`` points to the workflow repository and descriptor metadata
* ``cellpose_job`` points to the job script inside ``slurm_script_path`` or the cloned scripts repository
* any ``cellpose_job_<name>=<value>`` entry is translated to `` --<name>=<value>`` on the sbatch command line

Those per-workflow sbatch parameters override script defaults and also override
shared GPU fallback defaults when they set the same concept such as ``partition`` or ``gres``.

Example ``slurm-config.ini`` Notes
-----------------------------------------

The example file in ``resources/slurm-config.ini`` documents the newer
client-level options introduced, including:

* ``sacct_start_time``
* ``sacct_days_ago``
* ``env_file_submission``
* ``inject_gpu_flag``
* ``gpu_partition``
* ``gpu_gres``
* ``gpu_resource_flag``
* ``slurm_image_pull_via_sbatch``
* ``image_pull_cpus``
* ``image_pull_mem``
* ``apptainer_tmpdir``
* ``apptainer_cachedir``
* ``slurm_zip_cmd``


Troubleshooting
---------------

Common Issues and Solutions
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Container Access Errors
^^^^^^^^^^^^^^^^^^^^^^^

**Problem:** Workflows fail with "file not found" or permission errors.

**Solutions:**

1. **Check bind paths:** Configure ``slurm_data_bind_path`` if required.
2. **Verify permissions:** Ensure SLURM user can access data directories.
3. **Check container binding:** Verify Singularity or Apptainer can access the required paths.

.. code-block:: ini

  # Add explicit binding if needed
  slurm_data_bind_path=/data/your-scratch/data

SSH Connection Issues
^^^^^^^^^^^^^^^^^^^^^

**Problem:** Cannot connect to the SLURM cluster.

**Solutions:**

1. **SSH config:** Verify SSH configuration for the BIOMERO SSH host alias.
2. **Authentication:** Check SSH keys and authentication methods.
3. **Network:** Confirm network connectivity to the cluster.

.. code-block:: bash

  # Test SSH connection manually
  ssh your-slurm-host

  # Check SSH config
  ssh -F ~/.ssh/config your-slurm-host

Job Submission Failures
^^^^^^^^^^^^^^^^^^^^^^^

**Problem:** Jobs fail to submit or execute.

**Solutions:**

1. **Partition access:** Check whether the specified partition is available.
2. **Resource limits:** Verify memory, CPU, and GPU requests are within limits.
3. **Queue policies:** Check SLURM queue policies and restrictions.

.. code-block:: ini

  # Use appropriate partition
  cellpose_job_partition=gpu-partition

  # Adjust resource requests
  cellpose_job_mem=8GB
  cellpose_job_gres=gpu:1

Path Configuration Issues
^^^^^^^^^^^^^^^^^^^^^^^^^

**Problem:** Containers or scripts not found.

**Solutions:**

1. **Absolute vs relative paths:** Use the path format that matches your cluster setup.
2. **Directory existence:** Verify directories exist on the SLURM cluster.
3. **Path permissions:** Check read and write permissions.

.. code-block:: ini

  # Relative to home directory
  slurm_data_path=my-scratch/data

  # Or absolute path
  slurm_data_path=/data/users/username/my-scratch/data

Jobs do not see workflow parameters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Jobs start, but workflow parameters or paths appear empty inside the job environment.

**What to check:**

1. Enable ``env_file_submission=true`` if the cluster does not reliably propagate SSH session environment variables into ``sbatch`` jobs.
2. Verify that the generated or installed job script accepts the env-file argument as ``$1`` when env-file submission is enabled.
3. Review cluster SSH policy if ``AcceptEnv`` or similar controls are known to be restrictive.

Containers cannot access transferred data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** The BIOMERO transfer succeeds, but the container still cannot see the files.

**What to check:**

1. Set ``slurm_data_bind_path`` if the container runtime requires an explicit bind path.
2. Confirm that the configured bind path includes the effective ``slurm_data_path`` location.
3. Compare the cluster's default bind policy with the paths used by BIOMERO.

GPU jobs land on the wrong resources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** GPU jobs run on the wrong partition or with the wrong GRES settings.

**What to check:**

1. per-workflow ``*_job_partition`` and ``*_job_gres`` values in ``[MODELS]``
2. shared ``gpu_partition`` and ``gpu_gres`` values
3. whether ``inject_gpu_flag`` is enabled
4. whether the workflow was actually submitted with ``use_gpu=true``

Historical job listing returns too much or too little data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**Problem:** Historical job lookups return too much or too little data.

**What to check:**

1. Adjust ``sacct_start_time`` if you want a fixed absolute boundary.
2. Prefer ``sacct_days_ago`` if you want a rolling window.
3. Use the corresponding environment variables only when you need runtime-specific overrides.

FAQ
---

**Q: Should I use relative or absolute paths?**

A: Use relative paths if your SLURM setup expects paths relative to the user home directory. Use absolute paths if you need to specify exact filesystem locations.

**Q: When do I need to set ``slurm_data_bind_path``?**

A: Set this when your HPC administrator tells you to configure ``APPTAINER_BINDPATH``, or when containers cannot access your data directories.

**Q: How do I know which partition to use?**

A: Check with your HPC documentation or administrator. Common partitions include ``cpu``, ``gpu``, ``short``, and ``long``. Leave the setting empty to use the default.

**Q: Can I override job parameters for specific workflows?**

A: Yes. Add ``workflowname_job_parameter=value`` entries in the ``[MODELS]`` section to override default SLURM job parameters.

**Q: How do I debug workflow execution issues?**

A: Check SLURM job logs, verify container access to data, and ensure all required directories exist with proper permissions.

**Q: When should I enable ``env_file_submission``?**

A: Enable it when workflow jobs start correctly but do not receive the expected environment variables, especially on clusters where SSH environment forwarding into ``sbatch`` jobs is limited or disabled.

**Q: Why do ``gpu_partition`` and ``gpu_gres`` seem to do nothing?**

A: They are only used for workflow submissions when ``inject_gpu_flag`` is enabled and the workflow is submitted with ``use_gpu=true``. They also lose precedence to per-workflow ``*_job_partition`` and ``*_job_gres`` settings.

**Q: Should I prefer ``sacct_start_time`` or ``sacct_days_ago``?**

A: Use ``sacct_start_time`` for a fixed absolute boundary and ``sacct_days_ago`` for a rolling window. If both are set, ``sacct_days_ago`` takes precedence.

Further Reading
---------------

* :doc:`configuration-reference`
* `SLURM Documentation <https://slurm.schedmd.com/documentation.html>`_
* `Apptainer Documentation <https://apptainer.org/docs/>`_
* `Singularity User Guide <https://docs.sylabs.io/guides/latest/user-guide/>`_