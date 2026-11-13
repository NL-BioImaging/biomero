# -*- coding: utf-8 -*-
# Copyright 2024 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.  See the License for the specific language governing
# permissions and limitations under the License.
"""Durable metadata-maintenance requests, separate from analysis workflows.

Only plain request options and compact outcomes are persisted. The processor
owns execution; the scripts layer owns all external annotation operations.

Why this is in core and not in the processor or the scripts: a request is an
aggregate in the WorkflowTracker event store, next to the workflow aggregates.
Every projection of that store (``biomero.views``) resolves the class of each
event it reads from the event's stored topic, and the topic is the import path
of the class: ``biomero.maintenance:MetadataRefresh.Started`` and so on. A
class that only the processor or a script can import would make every other
process that replays the store fail on these events, and moving the class would
orphan the requests already stored. The request lifecycle (this aggregate and
its notification scan) therefore stays here; running a request does not.
"""
from collections import deque
from copy import deepcopy
import logging
from typing import Dict, Iterator, List, Optional, Set, Tuple
from uuid import UUID

from eventsourcing.domain import Aggregate, event
from eventsourcing.utils import get_topic

from biomero import constants
from biomero.constants import metadata_refresh_status as refresh_status

logger = logging.getLogger(__name__)

# Key of the failure count in a finished request's report (the report is the
# outcome of the scripts' refresh_all_metadata()).
REPORT_COUNTS_KEY = "counts"
REPORT_FAILED_KEY = "failed"


class MetadataRefresh(Aggregate):
    """A resumable maintenance request in the WorkflowTracker event store.

    The public methods guard the lifecycle and raise ValueError for a
    transition that cannot happen. The guards are not part of the events, so
    a stream written before they existed still replays.
    """

    def __init__(self, user: int, group: int, options: dict):
        self.user = user
        self.group = group
        self.options = deepcopy(options)
        self.status = refresh_status.QUEUED
        self.report = {}
        self.error = ""

    def is_finished(self) -> bool:
        """Whether the request reached a terminal state."""
        return self.status not in refresh_status.ACTIVE

    def started(self) -> None:
        """Mark the request as running; also done when a restart resumes it."""
        if self.is_finished():
            raise ValueError(f"Request {self.id} is already {self.status}; "
                             f"it cannot start again")
        self._started()

    def progressed(self, report: dict) -> None:
        """Record the progress report of a running request."""
        if self.status != refresh_status.RUNNING:
            raise ValueError(f"Request {self.id} is {self.status}; "
                             f"only a running request has progress")
        self._progressed(report)

    def finished(self, report: dict, error: str = "") -> None:
        """Record the outcome: FAILED on an error or failed targets, else DONE.

        Args:
            report (dict): The outcome of the refresh, with the failure count
                under ``report["counts"]["failed"]``.
            error (str, optional): Why the request could not run at all.
        """
        if self.is_finished():
            raise ValueError(f"Request {self.id} is already {self.status}; "
                             f"it cannot finish again")
        self._finished(report, error)

    @event("Started")
    def _started(self):
        self.status = refresh_status.RUNNING

    @event("Progress")
    def _progressed(self, report: dict):
        self.report = deepcopy(report)

    @event("Finished")
    def _finished(self, report: dict, error: str = ""):
        failed_targets = report.get(REPORT_COUNTS_KEY, {}).get(
            REPORT_FAILED_KEY, 0)
        has_failures = bool(error or failed_targets)
        self.report = deepcopy(report)
        self.error = error
        self.status = (refresh_status.FAILED if has_failures
                       else refresh_status.DONE)


def queue_metadata_refresh(tracker, user: int, group: int,
                           options: dict) -> UUID:
    """Persist the handoff before the requesting script closes its session.

    Args:
        tracker (WorkflowTracker): Tracker whose event store holds requests.
        user (int): OMERO user ID of the administrator who asked.
        group (int): OMERO group ID to run in.
        options (dict): Plain request options for the refresh.

    Returns:
        UUID: The request ID.
    """
    request = MetadataRefresh(user, group, options)
    tracker.save(request)
    tracker.commit_open_transaction()
    logger.info(f"Queued metadata refresh request {request.id} for user "
                f"{user} in group {group}")
    return request.id


def _lifecycle_topics() -> List[str]:
    """Topics of the events that decide whether a request is still pending."""
    return [get_topic(event_class) for event_class in (
        MetadataRefresh.Created, MetadataRefresh.Started,
        MetadataRefresh.Finished)]


def _lifecycle_notifications(tracker, start: int = 1) -> Iterator:
    """Yield the request lifecycle notifications from a cursor onwards.

    Pages through the notification log with only the request topics selected,
    so analysis events are never read.

    Args:
        tracker (WorkflowTracker): Tracker whose notification log to read.
        start (int, optional): Global notification ID to continue from; this
            is a notification cursor, not an aggregate version.

    Yields:
        Notification: In order of notification ID.
    """
    topics = _lifecycle_topics()
    page_size = min(constants.MAINTENANCE_SCAN_PAGE_SIZE,
                    tracker.notification_log.section_size)
    while True:
        notifications = tracker.notification_log.select(
            start=start, limit=page_size, topics=topics)
        yield from notifications
        if len(notifications) < page_size:
            return
        start = notifications[-1].id + 1


def pending_metadata_refreshes(
        tracker, start: int = 1,
        pending: Optional[Set[UUID]] = None) -> Tuple[int, Set[UUID]]:
    """Catch up on request notifications; a RUNNING request stays pending.

    A request that was RUNNING when its supervisor stopped has no Finished
    event, so it is pending again after a restart and gets resumed.

    Args:
        tracker (WorkflowTracker): Tracker whose notification log to read.
        start (int, optional): Notification cursor from a previous call.
        pending (set, optional): Pending request IDs from a previous call.

    Returns:
        tuple: The cursor to continue from, and the IDs of the requests that
            are queued or running.
    """
    pending = set(pending or ())
    finished_topic = get_topic(MetadataRefresh.Finished)
    for notification in _lifecycle_notifications(tracker, start):
        if notification.topic == finished_topic:
            pending.discard(notification.originator_id)
        else:
            pending.add(notification.originator_id)
        start = notification.id + 1
    logger.debug(f"Pending metadata refresh requests: {len(pending)} "
                 f"(cursor {start})")
    return start, pending


def metadata_refresh_statuses(
        tracker,
        recent: int = constants.MAINTENANCE_RECENT_REQUESTS) -> List[Dict]:
    """Plain snapshots of all active and the most recent finished requests.

    This replays the request notifications from the start on every call, which
    is meant for an occasional administrator status check.

    Args:
        tracker (WorkflowTracker): Tracker whose event store holds requests.
        recent (int, optional): How many finished requests to include.

    Returns:
        list: One dict per request, newest first.
    """
    pending = set()
    finished = deque(maxlen=recent)
    finished_topic = get_topic(MetadataRefresh.Finished)
    for notification in _lifecycle_notifications(tracker):
        if notification.topic == finished_topic:
            pending.discard(notification.originator_id)
            finished.append(notification.originator_id)
        else:
            pending.add(notification.originator_id)
    requests = [tracker.repository.get(request_id)
                for request_id in pending | set(finished)]
    requests.sort(key=lambda request: request.modified_on, reverse=True)
    return [{"request_id": str(request.id), "status": request.status,
             "created_on": request.created_on.isoformat(),
             "modified_on": request.modified_on.isoformat(),
             "options": deepcopy(request.options),
             "report": deepcopy(request.report),
             "error": request.error} for request in requests]
