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

Views are derived data and can be safely rebuilt from the event log:

1) Truncate view tables (optional):
   - ``biomero_job_view``, ``biomero_job_progress_view``, ``biomero_workflow_progress_view``, ``biomero_task_execution``
2) Reprocess events from the start with a System runner:

::

    from eventsourcing.system import System, SingleThreadedRunner
    from biomero import WorkflowTracker
    from biomero.views import JobAccounting, JobProgress, WorkflowProgress, WorkflowAnalytics

    system = System(pipes=[[WorkflowTracker, JobAccounting],
                           [WorkflowTracker, JobProgress],
                           [WorkflowTracker, WorkflowProgress],
                           [WorkflowTracker, WorkflowAnalytics]])
    runner = SingleThreadedRunner(system)
    runner.start()
    # Optionally call: runner.get(WorkflowTracker).pull_and_process(leader_name=WorkflowTracker.__name__)
    runner.stop()

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

Gotchas
-------

- When changing aggregates, add upcasters so old events can still be rehydrated.
- Rebuilding views is safe and preferred over complex data migrations.
- The rebuild process drops tables, so there will be brief downtime during the operation.
