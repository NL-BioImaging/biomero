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
* ``[ANALYTICS]`` for BIOMERO 2.x workflow tracking and provenance (database integration)
* ``[CONVERTERS]`` for external converter container images
* ``[WORKFLOWS]`` (or ``[MODELS]`` for legacy configs) for workflow repositories, job scripts, and per-workflow sbatch overrides

The ``[ANALYTICS]`` section enables BIOMERO 2.x provenance and workflow tracking.
See the `Analytics and Provenance Settings`_ section below for details.

See :doc:`developer/eventsourcing` for the event model and view table details.

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

   [WORKFLOWS]
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
The data conversion job is always submitted via ``sbatch``, so it honours the same
generic sbatch settings as workflow jobs.

Impact:

* If set, BIOMERO injects ``--partition=<value>`` as a real flag on the conversion
  ``sbatch`` command (and also exports ``CONVERSION_PARTITION`` to the conversion
  environment for custom scripts that read it).
* Precedence: ``slurm_conversion_partition`` wins over the generic
  ``slurm_default_partition`` fallback for conversion jobs.
* If unset, conversion falls back to ``slurm_default_partition`` (if configured),
  otherwise the cluster default partitioning behaviour.

Default (fallback) partition
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``slurm_default_partition`` on clusters that have no usable system default
partition, so you do not have to hard-code ``--partition`` into every workflow's
``[MODELS]`` job parameters. It applies to both workflow and conversion jobs.

Impact:

* If set, BIOMERO appends ``--partition=<value>`` to a workflow **or conversion**
  submission only when the job does not already carry a ``--partition`` directive.
* Precedence: for workflows, a per-workflow ``--partition`` in ``[MODELS]`` wins,
  and the GPU partition (from ``inject_gpu_flag`` or a per-workflow ``_use_gpu``)
  wins. For conversion, ``slurm_conversion_partition`` wins. The default partition
  is a last-resort fallback in both cases.
* If unset (default), no ``--partition`` is injected and the cluster default is
  used, so existing deployments are unaffected.

Overridable via the ``BIOMERO_DEFAULT_PARTITION`` environment variable
(precedence: code default ``None`` → ``slurm_default_partition`` in
``slurm-config.ini`` → ``BIOMERO_DEFAULT_PARTITION``).

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

This setting controls two related but distinct behaviours:

1. **``--nv`` in the generated script** — when ``false``, ``--nv`` is baked directly into the generated job script at script-generation time (``singularity run --nv ...``).  The script always passes ``--nv`` to the container runtime regardless of what happens at submission time.  When ``true``, the generated script instead contains a shell variable reference ``$GPU_FLAG``, and BIOMERO sets that variable to ``--nv`` or an empty string at submission time based on ``use_gpu``.

2. **Runtime toggling** — when ``false``, whether GPU sbatch resource params (``--partition``, ``--gres`` / ``--gpus``) are added is decided at config time via ``<name>_use_gpu=true`` in ``[MODELS]``.  This cannot be changed per-run.  When ``true``, the caller can pass ``use_gpu=true`` or ``use_gpu=false`` at submission time to switch between GPU and CPU mode on a per-run basis.

In short: without ``inject_gpu_flag``, GPU is either always on (hardcoded ``--nv``) or never injected — the two are controlled independently at script generation and submission time.  With ``inject_gpu_flag``, one script covers both modes and the caller decides per run.

Impact:

* When ``false``: ``--nv`` is hardcoded in the generated script.  ``<name>_use_gpu=true`` in ``[MODELS]`` adds GPU sbatch resource params at submission, but the container flag is already fixed in the script.  No runtime override is possible.
* When ``true``: the script contains ``$GPU_FLAG``.  BIOMERO sets it to ``--nv`` (GPU) or empty string (CPU) at submission time based on the resolved ``use_gpu`` value.

Use this when:

* you want one generated job script to run in both CPU and GPU modes without maintaining two separate scripts
* you need to submit the same workflow to GPU or CPU resources depending on the dataset or queue availability
* your generated scripts use the standard ``singularity run $GPU_FLAG`` or ``apptainer run $GPU_FLAG`` pattern

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

``gpu_partition``, ``gpu_gres``, and ``gpu_gpus`` are shared client defaults used when adding GPU sbatch params to a workflow submission.  They are relevant in **both** GPU code paths:

* **Static path** (``inject_gpu_flag=false``, ``<name>_use_gpu=true`` in ``[MODELS]``): BIOMERO always adds these params for that workflow.  The caller cannot override it at submission time.
* **Dynamic path** (``inject_gpu_flag=true``): BIOMERO adds these params only when ``use_gpu`` resolves to true — either because the caller passed ``use_gpu=true``, or because ``<name>_use_gpu=true`` is set in ``[MODELS]`` and no explicit ``use_gpu`` argument was given.

``gpu_gres`` and ``gpu_gpus`` are mutually exclusive.  Set one or the other:

* ``gpu_gres`` — BIOMERO appends ``--gres=<value>`` to GPU workflow submissions.
  Use this when your cluster schedules GPUs via Generic RESources (``--gres``).
* ``gpu_gpus`` — BIOMERO appends ``--gpus=<value>`` to GPU workflow submissions.
  Use this when your cluster uses the ``--gpus`` flag (common on newer Slurm versions).

Setting both raises a ``ValueError`` at startup.

Impact:

* If a workflow already has per-workflow sbatch overrides such as ``cellpose_job_partition``, ``cellpose_job_gres``, or ``cellpose_job_gpus``, those per-workflow settings take precedence over these shared defaults.
* If no per-workflow ``--partition``, ``--gres``, or ``--gpus`` is already present, BIOMERO appends these defaults to the submission command.

Full precedence order for GPU resource params:

1. explicit per-workflow ``[MODELS]`` sbatch overrides (e.g. ``cellpose_job_gres``)
2. shared ``gpu_partition`` and ``gpu_gres`` / ``gpu_gpus`` defaults
3. nothing — BIOMERO adds no GPU resource arguments

Global sbatch parameters
~~~~~~~~~~~~~~~~~~~~~~~~~

Any ``[SLURM]`` key that starts with ``sbatch_`` is treated as a global sbatch parameter
that BIOMERO adds to every workflow **and conversion** submission.

The key pattern is ``sbatch_<flag>=<value>``, which produces ``--<flag>=<value>`` on the
sbatch command line.

Examples:

.. code-block:: ini

   [SLURM]
   sbatch_reservation=biomero
   sbatch_nice=1

This appends ``--reservation=biomero`` and ``--nice=1`` to every workflow job and to
each data conversion job.

Impact:

* Global params are applied after per-workflow ``[MODELS]`` sbatch overrides.
* If a per-workflow override already sets the same flag (e.g. ``cellpose_job_reservation``), the global default for that flag is skipped.
* For conversion jobs, a ``--partition`` set via ``slurm_conversion_partition`` (or the ``slurm_default_partition`` fallback) takes precedence over a global ``sbatch_partition``.
* Global params with empty values are ignored.
* There is no environment variable override for these — they are intentionally admin-only at config time.

Use this when:

* you want to apply a cluster-specific constraint (e.g. a reservation or QOS) to all workflow jobs
* you need a cluster-wide ``--nice`` or ``--account`` that applies unless overridden per workflow

ZIP command
~~~~~~~~~~~

``slurm_zip_cmd`` controls which archive command BIOMERO uses when it zips job output on the cluster.

Impact:

* If unset, BIOMERO defaults to ``$(command -v 7z || command -v 7za)``.
* If set, BIOMERO uses the configured command directly when generating the zip command.

Use this when:

* your cluster exposes only ``7za`` or only ``7z``
* auto-detection is not reliable in your environment

Analytics and Provenance Settings
----------------------------------

The ``[ANALYTICS]`` section in ``slurm-config.ini`` controls BIOMERO 2.x provenance
and workflow tracking. When enabled, BIOMERO records every workflow and job event into
a PostgreSQL event store (via eventsourcing_sqlalchemy). Lightweight view tables are
then derived from those events and exposed to Metabase dashboards in OMERO.biomero —
showing live workflow status, per-user job accounting (who ran what, useful for Slurm
resource accounting), timing, and failures across the full OMERO → Slurm → OMERO
lifecycle.

This is on by default since BIOMERO 2.x. The individual listeners can be enabled or
disabled independently. Turn off ``track_workflows=False`` only if you are running a
basic 1.x-style deployment without a PostgreSQL analytics database, in which case none
of the Metabase dashboard views will be populated.

See :doc:`developer/eventsourcing` for the event model and view table details.

Analytics view table rebuild window
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. warning::

   This is an advanced opt-in feature. Enabling it means jobs outside the cutoff
   window will **not appear** in analytics views. Only use it when full rebuilds
   are genuinely too slow for your installation.

When the SLURM Init script resets analytics view tables, BIOMERO replays the full
event history to rebuild the views. On large installations with thousands of events
this can take several minutes. By default, BIOMERO always does a full rebuild.

Two settings allow capping how far back the replay goes. They are configured under
``[ANALYTICS]``, not ``[SLURM]``:

* ``analytics_rebuild_start_time``: absolute cutoff date, format ``YYYY-MM-DD``
* ``analytics_rebuild_days_ago``: rolling window in days

Effective precedence:

1. built-in default: full rebuild from event ID 1
2. resolved ``analytics_rebuild_start_time``
3. resolved ``analytics_rebuild_days_ago`` (overrides absolute date when both are set)
4. values set via environment variables (highest priority)

Environment variable overrides:

* ``BIOMERO_ANALYTICS_REBUILD_START_TIME``
* ``BIOMERO_ANALYTICS_REBUILD_DAYS_AGO`` (takes highest priority; falls back gracefully on invalid input)

These settings can also be overridden for a single run via the **Rebuild From Days Ago**
and **Rebuild From Date** inputs on the SLURM Init OMERO script.

.. note::

   These settings only affect view table rebuilds during ``reset_tables=True`` init runs.
   They do not affect ``sacct`` history queries or what jobs appear in the Monitor.
   View tables built from a partial event history will not include older jobs.

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

The ``[WORKFLOWS]`` section (also accepted as ``[MODELS]`` for backward
compatibility with existing configurations) defines each workflow and optionally
adds per-workflow sbatch overrides.  When both sections are present in the same
file their entries are merged; ``[WORKFLOWS]`` entries take precedence on key
collisions.

Example:

.. code-block:: ini

   [WORKFLOWS]
   cellpose=cellpose
   cellpose_repo=https://github.com/TorecLuik/W_NucleiSegmentation-Cellpose/tree/v1.2.7
   cellpose_job=jobs/cellpose.sh
   cellpose_job_gres=gpu:1g.10gb:1
   cellpose_job_partition=gpu
   cellpose_job_mem=16GB
   cellpose_use_gpu=true

How BIOMERO interprets these keys:

* ``cellpose`` defines the subdirectory under ``slurm_images_path``
* ``cellpose_repo`` points to the workflow repository and descriptor metadata
* ``cellpose_job`` points to the job script inside ``slurm_script_path`` or the cloned scripts repository
* any ``cellpose_job_<name>=<value>`` entry is translated to `` --<name>=<value>`` on the sbatch command line
* ``cellpose_use_gpu=true`` marks this workflow as GPU-enabled by default, so BIOMERO activates GPU handling even without an explicit ``use_gpu`` argument at submission time

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
* ``gpu_gpus``
* ``sbatch_<key>`` (global sbatch params pattern)
* ``slurm_image_pull_via_sbatch``
* ``image_pull_cpus``
* ``image_pull_mem``
* ``apptainer_tmpdir``
* ``apptainer_cachedir``
* ``slurm_zip_cmd``
* ``analytics_rebuild_start_time``
* ``analytics_rebuild_days_ago``


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

A: There are two conditions that trigger GPU sbatch param injection:

* ``inject_gpu_flag=true`` and the workflow is submitted with ``use_gpu=true`` (or ``<name>_use_gpu=true`` in ``[MODELS]`` with no explicit ``use_gpu`` argument)
* ``inject_gpu_flag=false`` but ``<name>_use_gpu=true`` is set in ``[MODELS]`` — the static path

In both cases, per-workflow ``*_job_partition``, ``*_job_gres``, and ``*_job_gpus`` settings in ``[MODELS]`` take precedence and will suppress the shared defaults for those flags.

**Q: Should I prefer ``sacct_start_time`` or ``sacct_days_ago``?**

A: Use ``sacct_start_time`` for a fixed absolute boundary and ``sacct_days_ago`` for a rolling window. If both are set, ``sacct_days_ago`` takes precedence.

Further Reading
---------------

* :doc:`configuration-reference`
* `SLURM Documentation <https://slurm.schedmd.com/documentation.html>`_
* `Apptainer Documentation <https://apptainer.org/docs/>`_
* `Singularity User Guide <https://docs.sylabs.io/guides/latest/user-guide/>`_