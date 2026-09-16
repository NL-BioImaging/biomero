from biomero import maintenance
from biomero.eventsourcing import WorkflowTracker


def test_metadata_request_is_durable_and_not_an_analysis_workflow():
    with WorkflowTracker(env={'PERSISTENCE_MODULE': 'eventsourcing.popo'}) as tracker:
        options = {'view_version': 'v0', 'workers': 4, 'workflow_ids': ['chosen']}
        request_id = maintenance.queue_metadata_refresh(tracker, 1, 2, options)
        options['workers'] = 8
        request = tracker.repository.get(request_id)
        assert request.status == 'QUEUED'
        assert request.options['workers'] == 4
        assert not hasattr(request, 'tasks')
        cursor, pending = maintenance.pending_metadata_refreshes(tracker)
        assert pending == {request_id}
        request.started()
        tracker.save(request)
        cursor, pending = maintenance.pending_metadata_refreshes(tracker, cursor, pending)
        assert pending == {request_id}  # A restart can resume RUNNING maintenance.
        request.finished({'discovered': 3, 'counts': {'updated': 2, 'failed': 0}})
        tracker.save(request)
        _, pending = maintenance.pending_metadata_refreshes(tracker, cursor, pending)
        assert pending == set()
        assert tracker.repository.get(request_id).status == 'DONE'
        assert tracker.repository.get(request_id, version=1).status == 'QUEUED'


def test_partial_failures_and_exceptions_are_terminal():
    with WorkflowTracker(env={'PERSISTENCE_MODULE': 'eventsourcing.popo'}) as tracker:
        request_id = maintenance.queue_metadata_refresh(tracker, 1, 2, {})
        request = tracker.repository.get(request_id)
        request.finished({'counts': {'failed': 1}}, 'one target failed')
        tracker.save(request)
        assert tracker.repository.get(request_id).status == 'FAILED'
        assert maintenance.pending_metadata_refreshes(tracker)[1] == set()


def test_candidate_discovery_filters_topics_and_drains_multiple_pages():
    with WorkflowTracker(env={'PERSISTENCE_MODULE': 'eventsourcing.popo'}) as tracker:
        expected = {maintenance.queue_metadata_refresh(tracker, 1, 2, {}) for _ in range(105)}
        assert maintenance.pending_metadata_refreshes(tracker)[1] == expected


def test_status_includes_all_active_requests_and_bounded_recent_history():
    with WorkflowTracker(env={'PERSISTENCE_MODULE': 'eventsourcing.popo'}) as tracker:
        active_id = maintenance.queue_metadata_refresh(tracker, 1, 2, {})
        finished_ids = []
        for _ in range(12):
            request_id = maintenance.queue_metadata_refresh(tracker, 1, 2, {})
            request = tracker.repository.get(request_id)
            request.finished({'discovered': 2, 'counts': {'updated': 2, 'failed': 0}})
            tracker.save(request)
            finished_ids.append(str(request_id))
        statuses = maintenance.metadata_refresh_statuses(tracker, recent=3)
        assert {item['request_id'] for item in statuses} == {str(active_id), *finished_ids[-3:]}
        assert {item['status'] for item in statuses} == {'QUEUED', 'DONE'}
        assert all('created_on' in item and 'modified_on' in item for item in statuses)


def test_handoff_is_visible_to_an_independent_sql_reader(tmp_path):
    env = {'PERSISTENCE_MODULE': 'eventsourcing_sqlalchemy',
           'SQLALCHEMY_URL': 'sqlite:///' + str(tmp_path / 'maintenance.db')}
    with WorkflowTracker(env=env) as writer:
        request_id = maintenance.queue_metadata_refresh(writer, 1, 2, {'workers': 4})
        with WorkflowTracker(env=env) as reader:
            assert maintenance.pending_metadata_refreshes(reader)[1] == {request_id}
            assert reader.repository.get(request_id).status == 'QUEUED'
