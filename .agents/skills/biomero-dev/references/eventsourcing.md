# Event sourcing diagnostics

Use this reference when diagnosing workflow/task lifecycle, status ordering,
analytics projections, or provenance metadata. The developer overview is
`docs/developer/eventsourcing.rst`; this reference focuses on repeatable
inspection and repair.

## Keep the three layers separate

1. The `WorkflowTracker` notification log is the immutable, globally ordered
   event stream. Notification IDs are positions in this stream across all
   workflow and task aggregates.
2. `WorkflowTracker.repository.get()` replays events for one aggregate. Its
   `version` argument is that aggregate's version, starting at `0`; it is not a
   notification ID.
3. `WorkflowProgress`, `WorkflowAnalytics`, `JobProgress`, and `JobAccounting`
   are disposable SQL projections. Their tracking rows record which leader
   notification they have processed.

Inspect in that order. An event-row dump is not a reconstructed aggregate, and
a projection row is not the source of truth.

## Initialize a tracker

Prefer the configured client in a real deployment because it establishes the
correct persistence and SQLAlchemy environment:

```python
from biomero import (
    EngineManager,
    NoOpWorkflowTracker,
    SlurmClient,
    WorkflowTracker,
)

client = SlurmClient.from_config()
tracker = client.workflowTracker
if isinstance(tracker, NoOpWorkflowTracker):
    raise RuntimeError("Workflow tracking is disabled")
```

For a focused development setup, construct the client with only the listeners
needed for the investigation, then initialize without resetting tables:

```python
client = SlurmClient(
    track_workflows=True,
    enable_job_accounting=False,
    enable_job_progress=True,
    enable_workflow_analytics=False,
)
client.initialize_analytics_system(reset_tables=False)
```

The environment must point at the intended event store. In deployments this
normally means `PERSISTENCE_MODULE=eventsourcing_sqlalchemy` and the correct
`SQLALCHEMY_URL`. Never print credentials from configuration or environment.

## Inspect notifications

```python
from pprint import pprint

tracker.notification_log.section_size = 100
notifications = tracker.notification_log.select(start=1, limit=100)
for notification in notifications:
    pprint(notification.__dict__)
```

Use a narrow `start` and `limit` for large stores. The notification exposes the
aggregate UUID, aggregate version, event topic, and serialized event state. Use
it to prove global event order and whether an expected event was persisted.

## Replay current and historical aggregate state

Pass a `UUID` explicitly so inspection does not depend on persistence-layer
coercion:

```python
from pprint import pprint
from uuid import UUID

aggregate_id = UUID("747fc951-15ca-4b56-a19e-418e1db97d14")
latest = tracker.repository.get(aggregate_id)
pprint(latest.__dict__)

for version in range(latest.version + 1):
    aggregate = tracker.repository.get(aggregate_id, version=version)
    print(version)
    pprint(aggregate.__dict__)
```

Workflow and task UUIDs are separate aggregates with independent version
sequences. A workflow's `TaskAdded` event changes the workflow aggregate; the
task's `TaskStarted`, `StatusUpdated`, and `TaskCompleted` events change the task
aggregate. Replay both UUIDs when reconstructing a lifecycle.

Prefer printing selected fields when task parameters or results are large:

```python
print({
    "version": latest.version,
    "status": getattr(latest, "status", None),
    "job_ids": getattr(latest, "job_ids", []),
    "has_result": bool(getattr(latest, "result_message", None)),
    "modified_on": latest._modified_on,
})
```

`TaskCompleted` currently records `result_message`; it does not itself assign a
terminal value to `Task.status`. Interpret the event history as well as the
status field, and preserve this behavior when assessing backward compatibility.

## Compare aggregates, projections, and exported metadata

Check these questions independently:

- Does the expected event exist in the notification log?
- Does replaying the aggregate through that version produce the expected state?
- Has each listener processed the corresponding notification ID?
- Does the SQL projection represent that state correctly?
- Was exported OMERO metadata generated before or after the terminal events?

Useful projection tables are:

- `biomero_workflow_progress_view`
- `biomero_task_execution`
- `biomero_job_progress_view`
- `biomero_job_view`

The listener tracking tables contain `application_name` and `notification_id`.
Compare their notification IDs with the latest relevant leader notification to
distinguish projection lag from a projection-policy bug.

Metadata code that calls `repository.get()` records the latest aggregate version
available at that moment, not the eventual final version. When metadata differs
from the final aggregate, compare its creation timestamp with event timestamps.
Do not publish internal coordination states (for example a worker claim) as a
user-facing execution status without an explicit mapping or provenance model.

## Catch up or rebuild projections

To process events into one existing listener without dropping tables:

```python
client.wfProgress.pull_and_process(
    leader_name=WorkflowTracker.__name__,
    start=1,
)
```

Choose the appropriate listener (`wfProgress`, `workflowAnalytics`,
`jobProgress`, or `jobAccounting`). Reprocessing relies on the projection's
tracking state and idempotent writes; inspect the tracking row afterward.

A full analytics reset is different:

```python
client.initialize_analytics_system(reset_tables=True)
```

`reset_tables=True` drops and recreates derived view and listener-tracking
tables, then replays events. It does not rewrite the immutable event store, but
it causes temporary analytics downtime. Use it only after a projection/schema
fix, with the target database verified and the rebuild coordinated. Never use a
full reset merely to inspect events or aggregates.

## Diagnose by failure boundary

- Missing or incorrect immutable event: fix the command/domain-event producer;
  rebuilding a view cannot repair it.
- Correct event but incorrect replayed aggregate: fix aggregate event handling
  or add an upcaster for historical schemas.
- Correct aggregate but stale projection: catch up the listener.
- Correct aggregate and caught-up listener but incorrect projection: fix the
  projection policy, test replay from the beginning, then rebuild the view.
- Correct final aggregate but stale exported metadata: fix metadata timing or
  its explicit status mapping; rebuilding SQL views will not change metadata
  already attached to OMERO objects.
