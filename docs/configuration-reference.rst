Configuration Reference
=======================

This page is a searchable lookup table for BIOMERO Python client configuration.
It focuses on options read by ``SlurmClient.from_config()`` and their runtime impact.

Resolution And Precedence
-------------------------

For the settings on this page, BIOMERO resolves values in this order:

1. built-in defaults
2. values from ``slurm-config.ini``
3. environment variable overrides, when supported

For ``sacct`` history settings, relative-day settings override absolute-date settings.

Use this page as the quick lookup companion to :doc:`slurm-configuration`, which
explains when and why you would choose each setting.

Environment Variable Lookup Table
---------------------------------

.. list-table:: Supported configuration env vars
   :header-rows: 1

   * - Environment variable
     - ``slurm-config.ini`` key
     - Type
     - Runtime effect
   * - ``BIOMERO_SACCT_START_TIME``
     - ``sacct_start_time``
     - string date
     - Absolute default start date for job history queries via ``sacct``
   * - ``BIOMERO_SACCT_START_DAYS_AGO``
     - ``sacct_days_ago``
     - integer
     - Rolling history window in days; overrides absolute start time when valid
   * - ``BIOMERO_ENV_FILE_SUBMISSION``
     - ``env_file_submission``
     - boolean
     - Switches submission from direct env passing to per-job env-file submission
   * - ``BIOMERO_INJECT_GPU_FLAG``
     - ``inject_gpu_flag``
     - boolean
     - Enables dynamic GPU flag handling in generated scripts and GPU-aware submissions
   * - ``BIOMERO_GPU_PARTITION``
     - ``gpu_partition``
     - string
     - Default fallback GPU partition for workflow submissions when GPU mode is requested
   * - ``BIOMERO_GPU_GRES``
     - ``gpu_gres``
     - string
     - Default fallback ``--gres=`` value appended to GPU workflow submissions; mutually exclusive with ``gpu_gpus``
   * - ``BIOMERO_GPU_GPUS``
     - ``gpu_gpus``
     - string
     - Default fallback ``--gpus=`` value appended to GPU workflow submissions; mutually exclusive with ``gpu_gres``
   * - ``BIOMERO_IMAGE_PULL_VIA_SBATCH``
     - ``slurm_image_pull_via_sbatch``
     - boolean
     - Switches workflow/converter image pulls from direct remote execution to sbatch jobs
   * - ``BIOMERO_PULL_CPUS``
     - ``image_pull_cpus``
     - string
     - CPU request used for sbatch-based image pull/build jobs
   * - ``BIOMERO_PULL_MEM``
     - ``image_pull_mem``
     - string
     - Memory request used for sbatch-based image pull/build jobs
   * - ``BIOMERO_APPTAINER_TMPDIR``
     - ``apptainer_tmpdir``
     - string
     - Tmp directory exported for Apptainer/Singularity image pull/build commands
   * - ``BIOMERO_APPTAINER_CACHEDIR``
     - ``apptainer_cachedir``
     - string
     - Cache directory exported for Apptainer/Singularity image pull/build commands
   * - ``BIOMERO_SLURM_ZIP_CMD``
     - ``slurm_zip_cmd``
     - string
     - Overrides the zip command used on the cluster for packaging results
   * - ``BIOMERO_ANALYTICS_REBUILD_START_TIME``
     - ``analytics_rebuild_start_time``
     - string date
     - Absolute cutoff date (``YYYY-MM-DD``) from which events are replayed when resetting analytics view tables; leave unset to replay all events
   * - ``BIOMERO_ANALYTICS_REBUILD_DAYS_AGO``
     - ``analytics_rebuild_days_ago``
     - integer
     - Rolling cutoff window in days for analytics view table rebuilds; overrides the absolute date when set
   * - ``GPU_PARTITION``
     - ``gpu_partition``
     - string
     - Legacy fallback env var for GPU partition; lower priority than ``BIOMERO_GPU_PARTITION``
   * - ``GPU_GRES``
     - ``gpu_gres``
     - string
     - Legacy fallback env var for GPU gres; lower priority than ``BIOMERO_GPU_GRES``
   * - ``GPU_GPUS``
     - ``gpu_gpus``
     - string
     - Legacy fallback env var for GPU gpus; lower priority than ``BIOMERO_GPU_GPUS``

``slurm-config.ini`` Lookup Table
---------------------------------

.. list-table:: Key ``[SLURM]`` options
   :header-rows: 1

   * - Key
     - Type
     - Default
     - Effect
   * - ``slurm_data_path``
     - string
     - ``my-scratch/data``
     - Base path for transferred workflow data on the cluster
   * - ``slurm_images_path``
     - string
     - ``my-scratch/singularity_images/workflows``
     - Base path for workflow containers on the cluster
   * - ``slurm_converters_path``
     - string
     - ``my-scratch/singularity_images/converters``
     - Base path for converter containers on the cluster
   * - ``slurm_script_path``
     - string
     - ``slurm-scripts``
     - Base path for generated or cloned job scripts
   * - ``slurm_script_repo``
     - string or empty
     - empty
     - If set, BIOMERO clones and updates job scripts from this repository instead of generating scripts locally; use with caution, because external script repos can drift out of sync with newer BIOMERO releases and are not the recommended default
   * - ``slurm_data_bind_path``
     - string or empty
     - unset
     - Injects ``APPTAINER_BINDPATH`` into job environments
   * - ``slurm_conversion_partition``
     - string or empty
     - unset
     - Partition for data conversion jobs; injected as a real ``--partition`` flag on the conversion sbatch (and exported as ``CONVERSION_PARTITION``). Takes precedence over ``slurm_default_partition`` for conversion jobs
   * - ``slurm_default_partition``
     - string or empty
     - unset
     - Generic fallback ``--partition`` appended to workflow **and** conversion jobs that do not already set a partition; per-workflow params, the GPU partition, and ``slurm_conversion_partition`` take precedence. Overridable via ``BIOMERO_DEFAULT_PARTITION``
   * - ``sacct_start_time``
     - string date or empty
     - ``2023-01-01``
     - Absolute start date used when listing historical jobs
   * - ``sacct_days_ago``
     - integer or empty
     - unset
     - Relative history window; takes precedence over ``sacct_start_time``
   * - ``env_file_submission``
     - boolean
     - ``false``
     - Uses env-file based submission instead of direct environment propagation
   * - ``inject_gpu_flag``
     - boolean
     - ``false``
     - Enables dynamic GPU submission support in generated scripts
   * - ``gpu_partition``
     - string or empty
     - unset
     - Shared fallback partition appended for GPU workflow runs when needed
   * - ``gpu_gres``
     - string or empty
     - unset
     - Shared fallback ``--gres=`` value appended for GPU workflow runs; mutually exclusive with ``gpu_gpus``
   * - ``gpu_gpus``
     - string or empty
     - unset
     - Shared fallback ``--gpus=`` value appended for GPU workflow runs; mutually exclusive with ``gpu_gres``
   * - ``sbatch_<key>``
     - string or empty
     - unset
     - Any ``[SLURM]`` key starting with ``sbatch_`` adds ``--<key>=<value>`` to every workflow **and conversion** submission; per-workflow sbatch overrides (and ``slurm_conversion_partition`` for the conversion ``--partition``) always take precedence
   * - ``slurm_image_pull_via_sbatch``
     - boolean
     - ``false``
     - Uses sbatch jobs instead of direct remote execution for workflow/converter image pulls
   * - ``image_pull_cpus``
     - string
     - ``8``
     - CPU request for sbatch-based image pull/build jobs
   * - ``image_pull_mem``
     - string
     - ``32G``
     - Memory request for sbatch-based image pull/build jobs
   * - ``apptainer_tmpdir``
     - string or empty
     - unset
     - Tmp directory exported for Apptainer/Singularity image pull/build commands
   * - ``apptainer_cachedir``
     - string or empty
     - unset
     - Cache directory exported for Apptainer/Singularity image pull/build commands
   * - ``slurm_zip_cmd``
     - string or empty
     - ``$(command -v 7z || command -v 7za)``
     - Command used for result zipping on the HPC
   * - ``analytics_rebuild_start_time``
     - string date or empty
     - unset
     - Absolute cutoff date (``YYYY-MM-DD``) from which events are replayed when resetting analytics view tables; leave unset to replay all events. Configured under ``[ANALYTICS]``.
   * - ``analytics_rebuild_days_ago``
     - integer or empty
     - unset
     - Rolling cutoff window in days for analytics view table rebuilds; overrides the absolute date when set. Configured under ``[ANALYTICS]``.
   * - ``<name>_use_gpu``
     - boolean
     - ``false``
     - Per-workflow GPU default in ``[MODELS]``; e.g. ``cellpose_use_gpu=true`` marks a workflow as GPU-enabled so BIOMERO activates GPU handling without requiring an explicit ``use_gpu`` argument at submission time

Behaviour Notes
---------------

Boolean parsing
~~~~~~~~~~~~~~~

Supported truthy values are:

* ``true``
* ``1``
* ``yes``
* ``y``
* ``on``

Supported falsy values are:

* ``false``
* ``0``
* ``no``
* ``n``
* ``off``

Invalid boolean env var values fall back to the already resolved ini or default value.

Integer parsing
~~~~~~~~~~~~~~~

Invalid integer env var values also fall back to the already resolved ini or default value.
This matters especially for ``BIOMERO_SACCT_START_DAYS_AGO``.

Empty-string handling
~~~~~~~~~~~~~~~~~~~~~

Some optional string settings treat an empty value as unset rather than as a literal empty string.
This is relevant for values such as ``slurm_data_bind_path``, ``slurm_conversion_partition``,
``slurm_default_partition``, ``gpu_partition``, ``gpu_gres``, ``gpu_gpus``, and the optional ``sacct`` window settings.

GPU precedence
~~~~~~~~~~~~~~

For workflow submissions, GPU-related settings are applied in this order:

1. per-workflow sbatch parameters from ``[MODELS]`` such as ``cellpose_job_partition``, ``cellpose_job_gres``, and ``cellpose_job_gpus``
2. global sbatch defaults from ``sbatch_<key>`` entries in ``[SLURM]``
3. shared runtime GPU defaults: ``gpu_partition`` and either ``gpu_gres`` (``--gres=``) or ``gpu_gpus`` (``--gpus=``); these two are mutually exclusive — set one or the other, never both
4. no BIOMERO-added GPU resource arguments

Shared GPU defaults (level 3) are considered when GPU mode is active.  GPU mode is activated by one of two code paths:

* **Dynamic path** (``inject_gpu_flag=true``): ``use_gpu`` is resolved at submission time — explicit ``use_gpu`` argument wins, otherwise falls back to ``<name>_use_gpu`` from ``[MODELS]``.  ``GPU_FLAG`` env var is set (``--nv`` or empty) at submission time so the script can toggle the container runtime flag.
* **Static path** (``inject_gpu_flag=false``): ``--nv`` is baked into the generated script at script-generation time; only ``<name>_use_gpu=true`` in ``[MODELS]`` triggers GPU sbatch resource param injection, and it cannot be overridden at submission time.  No ``GPU_FLAG`` env var is set.

See :doc:`slurm-configuration` for a full explanation of the interaction between these settings.

Search Hints
------------

If you are looking for a specific name, try searching the docs for any of these exact identifiers:

* ``BIOMERO_SACCT_START_TIME``
* ``BIOMERO_SACCT_START_DAYS_AGO``
* ``BIOMERO_ENV_FILE_SUBMISSION``
* ``BIOMERO_INJECT_GPU_FLAG``
* ``BIOMERO_GPU_PARTITION``
* ``BIOMERO_GPU_GRES``
* ``BIOMERO_GPU_GPUS``
* ``BIOMERO_IMAGE_PULL_VIA_SBATCH``
* ``BIOMERO_PULL_CPUS``
* ``BIOMERO_PULL_MEM``
* ``BIOMERO_APPTAINER_TMPDIR``
* ``BIOMERO_APPTAINER_CACHEDIR``
* ``BIOMERO_SLURM_ZIP_CMD``
* ``BIOMERO_ANALYTICS_REBUILD_START_TIME``
* ``BIOMERO_ANALYTICS_REBUILD_DAYS_AGO``
* ``GPU_PARTITION``
* ``GPU_GRES``
* ``GPU_GPUS``