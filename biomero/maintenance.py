"""Durable metadata-maintenance requests, separate from analysis workflows.

Only plain request options and compact outcomes are persisted. The processor
owns execution; the scripts layer owns all external annotation operations.
"""
from copy import deepcopy
from collections import deque

from eventsourcing.domain import Aggregate, event
from eventsourcing.utils import get_topic


class MetadataRefresh(Aggregate):
    """A resumable maintenance request in the WorkflowTracker event store."""

    def __init__(self, user: int, group: int, options: dict):
        self.user = user
        self.group = group
        self.options = deepcopy(options)
        self.status = 'QUEUED'
        self.report = {}
        self.error = ''

    @event('Started')
    def started(self):
        self.status = 'RUNNING'

    @event('Progress')
    def progressed(self, report: dict):
        self.report = deepcopy(report)

    @event('Finished')
    def finished(self, report: dict, error: str = ''):
        self.report = deepcopy(report)
        self.error = error
        self.status = 'FAILED' if error or report.get('counts', {}).get('failed', 0) else 'DONE'


def queue_metadata_refresh(tracker, user, group, options):
    """Persist the handoff before the requesting script closes its session."""
    request = MetadataRefresh(user, group, options)
    tracker.save(request)
    # Scoped datastores delegate transaction ownership to their caller.
    datastore = getattr(tracker.factory, 'datastore', None)
    if datastore is not None and getattr(datastore, 'scoped_session', None) is not None:
        datastore.scoped_session.commit()
    return request.id


def pending_metadata_refreshes(tracker, start=1, pending=None):
    """Catch up only maintenance notifications; recover RUNNING after restart.

    ``start`` is a global notification cursor, not an aggregate version.
    A fresh supervisor replays this small, topic-filtered stream once. Its
    subsequent polls continue at the returned cursor.
    """
    pending = set(pending or ())
    topics = [get_topic(cls) for cls in (
        MetadataRefresh.Created, MetadataRefresh.Started, MetadataRefresh.Finished)]
    limit = min(100, tracker.notification_log.section_size)
    while True:
        notifications = tracker.notification_log.select(start=start, limit=limit, topics=topics)
        for notification in notifications:
            request_id = notification.originator_id
            if notification.topic == topics[2]:
                pending.discard(request_id)
            else:
                pending.add(request_id)
            start = notification.id + 1
        if len(notifications) < limit:
            return start, pending


def metadata_refresh_statuses(tracker, recent=5):
    """Return plain snapshots of all active and bounded recent terminal requests."""
    pending = set()
    finished = deque(maxlen=recent)
    start = 1
    limit = min(100, tracker.notification_log.section_size)
    topics = [get_topic(cls) for cls in (
        MetadataRefresh.Created, MetadataRefresh.Started, MetadataRefresh.Finished)]
    while True:
        notifications = tracker.notification_log.select(start=start, limit=limit, topics=topics)
        for notification in notifications:
            request_id = notification.originator_id
            if notification.topic == topics[2]:
                pending.discard(request_id)
                finished.append(request_id)
            else:
                pending.add(request_id)
            start = notification.id + 1
        if len(notifications) < limit:
            break
    requests = [tracker.repository.get(request_id) for request_id in pending | set(finished)]
    requests.sort(key=lambda request: request.modified_on, reverse=True)
    return [{'request_id': str(request.id), 'status': request.status,
             'created_on': request.created_on.isoformat(),
             'modified_on': request.modified_on.isoformat(),
             'options': deepcopy(request.options), 'report': deepcopy(request.report),
             'error': request.error} for request in requests]
