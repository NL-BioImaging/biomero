Eventsourcing and Views (Developer)
===================================

This page explains how BIOMERO tracks workflow execution using eventsourcing, and how read models ("views") are maintained and migrated.


Overview
--------

- Event side: domain aggregates emit immutable events and are stored in an event store (via eventsourcing_sqlalchemy).
- View side: lightweight SQLAlchemy models are updated by ProcessApplications that listen to events and persist denormalized rows for fast queries and dashboards.


Event side
----------

- Aggregates: see ``biomero.eventsourcing``
  - ``WorkflowRun`` (create/start/complete/fail; holds list of task IDs)
  - ``Task`` (create/start/complete/fail; adds Slurm job IDs; status/progress; results)
- Application service: ``WorkflowTracker`` orchestrates aggregate lifecycle methods and commits using ``EngineManager``.
- Persistence: the event store is managed by the eventsourcing library.

Environment
~~~~~~~~~~~

- Required env vars:
  - ``PERSISTENCE_MODULE=eventsourcing_sqlalchemy``
  - ``SQLALCHEMY_URL=postgresql+psycopg2://...`` (or sqlite for tests)
- Engine wiring: ``EngineManager.create_scoped_session()`` configures the SQLAlchemy engine/session used both by eventsourcing and the views.

Versioning aggregates
~~~~~~~~~~~~~~~~~~~~~

When changing aggregate or event schemas, keep backward compatibility with stored events:

- Bump ``INITIAL_VERSION`` (or class_version) as appropriate.
- Add ``upcast_vX_vY(state)`` static methods on Aggregate/Event classes to adapt older event snapshots to the new shape.
- See the eventsourcing docs for versioning patterns.


View side
---------

Views are updated by ProcessApplications that consume events and persist into BIOMERO-owned tables (all start with ``biomero_...``):

- ``biomero.views.JobAccounting`` -> ``biomero_job_view`` (user/group + task_id per Slurm job)
- ``biomero.views.JobProgress`` -> ``biomero_job_progress_view`` (status/progress per Slurm job)
- ``biomero.views.WorkflowProgress`` -> ``biomero_workflow_progress_view`` (status/progress/name/user/group/task)
- ``biomero.views.WorkflowAnalytics`` -> ``biomero_task_execution`` (per-task analytics, timings, failures)

These are standard SQLAlchemy models defined in ``biomero.database``. Schema changes are applied via event sourcing system rebuild.

Rebuilding views (reprojection)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Views are derived data. To catch up an existing progress listener, use the
configured client and invoke the follower, not ``WorkflowTracker`` itself:

.. code-block:: python

    from biomero import SlurmClient, WorkflowTracker

    client = SlurmClient.from_config()
    client.wfProgress.pull_and_process(
        leader_name=WorkflowTracker.__name__, start=1)

The listener's persisted tracking position determines which notifications have
already been processed. This is not a forced rebuild. Do not truncate view
rows alone: retained listener positions can prevent the deleted rows from
being reconstructed. For a full rebuild, use the reset operation below, which
also resets listener tracking. Coordinate it with other database users.

Notes:
- View upserts use ``session.merge(...)`` or primary keys to stay idempotent.
- If you change primary keys or uniqueness, do a one-off cleanup before reprojecting.

Schema changes (views only)
---------------------------

BIOMERO view tables are managed via the event sourcing system rebuild mechanism.
The event store tables are managed by the eventsourcing library.

Typical workflow for view schema changes:

1) Edit SQLAlchemy models in ``biomero.database`` (BIOMERO view tables only).
2) Use the event sourcing rebuild to apply changes:

::

    # In Python code or SLURM Init script:
    client.initialize_analytics_system(reset_tables=True)

This will drop and recreate all view tables with the new schema, then replay
all events to repopulate them with the updated structure.

Metadata maintenance requests
-----------------------------

``biomero.maintenance.MetadataRefresh`` stores administrative metadata-refresh
requests in the same ``WorkflowTracker`` event store. The class stays in core
because every projection of that store resolves an event's class from its stored
topic (the import path of the class); the lifecycle guards, the topics and the
notification scan live with it, while running a request is the processor's job.
Requests are kept separate from
``WorkflowRun`` and ``Task``. These requests do not create analysis projection
rows. The aggregate contains plain options, requester IDs, lifecycle status,
progress counters and a compact final outcome; annotation operations remain in
the scripts layer. Inspect it with ``tracker.repository.get(UUID(request_id))``
and use its aggregate versions for historical replay. The processor discovers
unfinished requests through topic-filtered notifications and retries interrupted
idempotent sweeps. See NL-BIOMERO's developer supervisor documentation for the
execution and recovery policy.

Detached launcher claim and cancellation
----------------------------------------

A detached run is queued as a *launcher task*: a task of a launching script
whose params carry ``constants.LAUNCHER_MARKER``; a script adds it with
``WorkflowTracker.add_launcher_task``. Every reader (views,
provenance, the supervisor) decides with the one rule
``biomero.eventsourcing.is_launcher_task``, and finds the task with
``WorkflowTracker.find_launcher_task(workflow_id)``. The scripts decide what a
launcher records and how a run resumes; the supervisor decides what to run.

A supervisor takes a launcher with ``WorkflowTracker.claim_launcher_task(task_id,
owner)``. The claim is a ``Task.LauncherClaimed`` event appended to the task's
stream, so it is a compare-and-set: when two supervisors read the same
unclaimed task and both claim it, the event store accepts one and the other
call returns ``False``. A claim is valid for
``constants.LAUNCHER_CLAIM_LEASE_SECONDS``; its owner renews it by claiming
again, and a claim that is not renewed can be taken over. A supervisor that
stops cleanly gives its claim up with ``release_launcher_task``. The claim does
not change ``Task.status``. Launchers claimed by 2.9 betas carry the status
``CLAIMED`` and no owner; they still load, the views still ignore that status,
and such a launcher is free to claim.

``WorkflowTracker.request_workflow_cancel(workflow_id, reason)`` records a
``WorkflowRun.CancelRequested`` event. Whoever executes the workflow reads it
with ``is_workflow_cancel_requested``, cancels its Slurm jobs with
``SlurmClient.cancel_slurm_jobs`` and fails the workflow.

Inspecting aggregate history
----------------------------

Notification IDs are global event-log positions. Aggregate versions belong to
one workflow, task or maintenance request; they are not interchangeable.
Workflow and task UUIDs identify separate aggregates.

.. code-block:: python

   from uuid import UUID

   tracker = client.workflowTracker
   notifications = tracker.notification_log.select(start=1, limit=10)
   workflow = tracker.repository.get(UUID(workflow_id))
   previous = tracker.repository.get(UUID(workflow_id), version=8)
   task = tracker.repository.get(workflow.tasks[0])

Use an existing aggregate version when inspecting a real workflow. Tracking
must be enabled and the client must point at the intended persistent store.
Reading an aggregate does not run or resume it. ``TaskCompleted`` records
``result_message`` without necessarily replacing the task's ``status`` field;
inspect lifecycle events as well as status strings.

When comparing detached and inline runs, account for the additional launcher
task. ``CLAIMED`` is a coordination state and is not analysis ``RUNNING``.
Remote shallowing adds its own helper task and receipts. These records remain
in history even when excluded from the searchable metadata view. See
:doc:`execution-and-storage` and :doc:`metadata-views`.

Gotchas
-------

- When changing aggregates, add upcasters so old events can still be rehydrated.
- Rebuilding views is safe and preferred over complex data migrations.
- The rebuild process drops tables, so there will be brief downtime during the operation.
