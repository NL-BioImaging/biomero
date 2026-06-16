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
     - Default fallback GPU GRES for workflow submissions when GPU mode is requested
   * - ``BIOMERO_SLURM_ZIP_CMD``
     - ``slurm_zip_cmd``
     - string
     - Overrides the zip command used on the cluster for packaging results
   * - ``GPU_PARTITION``
     - ``gpu_partition``
     - string
     - Legacy fallback env var for GPU partition; lower priority than ``BIOMERO_GPU_PARTITION``
   * - ``GPU_GRES``
     - ``gpu_gres``
     - string
     - Legacy fallback env var for GPU gres; lower priority than ``BIOMERO_GPU_GRES``

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
     - Provides a partition hint for conversion jobs
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
     - Shared fallback gres appended for GPU workflow runs when needed
   * - ``slurm_zip_cmd``
     - string or empty
     - ``$(command -v 7z || command -v 7za)``
     - Command used for result zipping on the HPC

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
``gpu_partition``, ``gpu_gres``, and the optional ``sacct`` window settings.

GPU precedence
~~~~~~~~~~~~~~

For workflow submissions, GPU-related settings are applied in this order:

1. per-workflow sbatch parameters from ``[MODELS]`` such as ``cellpose_job_partition`` and ``cellpose_job_gres``
2. shared runtime defaults from ``gpu_partition`` and ``gpu_gres``
3. no BIOMERO-added GPU resource arguments

Shared defaults are only considered when both conditions hold:

* ``inject_gpu_flag`` is enabled
* the workflow is submitted with ``use_gpu=true``

Search Hints
------------

If you are looking for a specific name, try searching the docs for any of these exact identifiers:

* ``BIOMERO_ENV_FILE_SUBMISSION``
* ``BIOMERO_INJECT_GPU_FLAG``
* ``BIOMERO_GPU_PARTITION``
* ``BIOMERO_GPU_GRES``
* ``BIOMERO_SLURM_ZIP_CMD``
* ``BIOMERO_SACCT_START_TIME``
* ``BIOMERO_SACCT_START_DAYS_AGO``
* ``GPU_PARTITION``
* ``GPU_GRES``