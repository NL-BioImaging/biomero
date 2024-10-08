from datetime import datetime, timezone
import os
from unittest.mock import patch
import uuid
import pytest
from biomero.eventsourcing import Task, WorkflowTracker
from biomero.views import JobAccounting, JobProgress, WorkflowAnalytics, WorkflowProgress
from biomero.database import EngineManager, JobProgressView, JobView, TaskExecution
from biomero.constants import workflow_status as wfs
from uuid import UUID
import logging
from eventsourcing.system import System, SingleThreadedRunner

# Configure logging
logging.basicConfig(level=logging.INFO)

# Fixture for setting up the environment variables and session


@pytest.fixture(autouse=True)
def set_env_vars_and_session():
    # Set the necessary environment variables for testing
    os.environ["PERSISTENCE_MODULE"] = "eventsourcing_sqlalchemy"
    os.environ["SQLALCHEMY_URL"] = "sqlite:///:memory:"

    # Initialize the scoped session for testing
    EngineManager.create_scoped_session()

    # Yield control to the test function
    yield

    # Clean up environment variables after the test
    del os.environ["PERSISTENCE_MODULE"]
    del os.environ["SQLALCHEMY_URL"]

    # Close and clean up the database session
    EngineManager.close_engine()


@pytest.fixture
def workflow_tracker():
    # Fixture to set up the WorkflowTracker application
    return WorkflowTracker()


@pytest.fixture
def workflow_tracker_and_job_accounting():
    """Fixture to initialize System and SingleThreadedRunner with WorkflowTracker and JobAccounting."""
    # Create a System instance with the necessary components
    system = System(pipes=[[WorkflowTracker, JobAccounting]])
    runner = SingleThreadedRunner(system)
    runner.start()

    # Yield the instances of WorkflowTracker and JobAccounting
    yield runner.get(WorkflowTracker), runner.get(JobAccounting)

    # Cleanup after tests
    runner.stop()


@pytest.fixture
def workflow_tracker_and_job_progress():
    """Fixture to initialize System and SingleThreadedRunner with WorkflowTracker and JobProgress."""
    # Create a System instance with the necessary components
    system = System(pipes=[[WorkflowTracker, JobProgress]])
    runner = SingleThreadedRunner(system)
    runner.start()

    # Yield the instances of WorkflowTracker and JobProgress
    yield runner.get(WorkflowTracker), runner.get(JobProgress)

    # Cleanup after tests
    runner.stop()
    

@pytest.fixture
def workflow_tracker_and_workflow_progress():
    """Fixture to initialize System and SingleThreadedRunner with WorkflowTracker and JobProgress."""
    # Create a System instance with the necessary components
    system = System(pipes=[[WorkflowTracker, WorkflowProgress]])
    runner = SingleThreadedRunner(system)
    runner.start()

    # Yield the instances of WorkflowTracker and JobProgress
    yield runner.get(WorkflowTracker), runner.get(WorkflowProgress)

    # Cleanup after tests
    runner.stop()


@pytest.fixture
def workflow_tracker_and_workflow_analytics():
    """Fixture to initialize WorkflowTracker and WorkflowAnalytics."""
    # Create a System instance with the necessary components
    system = System(pipes=[[WorkflowTracker, WorkflowAnalytics]])
    runner = SingleThreadedRunner(system)
    runner.start()

    # Yield the instances of WorkflowTracker and WorkflowAnalytics
    yield runner.get(WorkflowTracker), runner.get(WorkflowAnalytics)

    # Cleanup after tests
    runner.stop()


def test_runner():
    # Create a System instance with the necessary components
    system = System(pipes=[[WorkflowTracker, JobAccounting]])
    runner = SingleThreadedRunner(system)
    runner.start()

    # Get the application
    wft = runner.get(WorkflowTracker)

    # when
    assert wft.closing.is_set() is False
    runner.stop()
    assert wft.closing.is_set() is True
    
    # runner.start()
    # wft2 = runner.get(WorkflowTracker)
    # assert wft2.closing.is_set() is False
    

def test_initiate_workflow(workflow_tracker):
    # Initiating a workflow
    workflow_id = workflow_tracker.initiate_workflow(
        name="Test Workflow",
        description="This is a test workflow",
        user=1,
        group=1
    )

    # Check if the returned ID is a valid UUID
    assert isinstance(workflow_id, UUID)

    # Check if the workflow has been added to the repository
    workflow = workflow_tracker.repository.get(workflow_id)
    assert workflow is not None
    assert workflow.name == "Test Workflow"
    assert workflow.description == "This is a test workflow"
    assert workflow.user == 1
    assert workflow.group == 1
    assert len(workflow.tasks) == 0  # Initially, no tasks should be present


def test_add_task_to_workflow(workflow_tracker):
    # Initiate a workflow
    workflow_id = workflow_tracker.initiate_workflow(
        name="Test Workflow with Task",
        description="Workflow description",
        user=1,
        group=1
    )

    # Add a task to the workflow
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id=workflow_id,
        task_name="Task 1",
        task_version="1.0",
        input_data={"input_key": "input_value"},
        kwargs={}
    )
    assert isinstance(task_id, UUID)

    # Check the task and workflow are updated
    workflow = workflow_tracker.repository.get(workflow_id)
    assert len(workflow.tasks) == 1
    assert workflow.tasks[0] == task_id
    task = workflow_tracker.repository.get(task_id)
    assert task.task_name == "Task 1"
    assert task.workflow_id == workflow_id


def test_start_workflow(workflow_tracker, caplog):
    """
    Test starting a workflow and checking for the WorkflowStarted event.
    """
    # Enable capturing of log messages
    with caplog.at_level("DEBUG"):
        # Initiate a workflow
        workflow_id = workflow_tracker.initiate_workflow(
            name="Workflow to Start",
            description="Description of workflow",
            user=1,
            group=1
        )

        # Start the workflow
        workflow_tracker.start_workflow(workflow_id)

        # Verify the workflow has emitted the WorkflowStarted event via logs
        assert any(
            "Starting workflow" in record.message and str(
                workflow_id) in record.message
            for record in caplog.records
        )


def test_complete_workflow(workflow_tracker):
    # Initiate, start, and complete a workflow
    workflow_id = workflow_tracker.initiate_workflow(
        name="Workflow to Complete",
        description="Description of workflow",
        user=1,
        group=1
    )
    workflow_tracker.start_workflow(workflow_id)
    workflow_tracker.complete_workflow(workflow_id)

    # Retrieve and print notifications
    notifications = workflow_tracker.notification_log.select(start=1, limit=10)

    # Sort notifications by ID to ensure proper ordering
    notifications_sorted = sorted(notifications, key=lambda n: n.id)

    # Define the expected order of topics
    expected_topics = [
        'biomero.eventsourcing:WorkflowRun.WorkflowInitiated',
        'biomero.eventsourcing:WorkflowRun.WorkflowStarted',
        'biomero.eventsourcing:WorkflowRun.WorkflowCompleted',
    ]

    # Extract topics in the order of sorted notifications
    actual_topics = [
        notification.topic for notification in notifications_sorted]

    # Check if the actual topics match the expected topics
    assert actual_topics == expected_topics, (
        f"Expected topics order: {expected_topics}, but got: {actual_topics}"
    )


def test_fail_workflow(workflow_tracker):
    # GIVEN a workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow(
        name="Workflow to Fail",
        description="Description of workflow",
        user=1,
        group=1
    )

    # WHEN the workflow is marked as failed
    workflow_tracker.fail_workflow(workflow_id, error_message="Some error")

    # THEN verify the workflow has a failure notification
    notifications = workflow_tracker.notification_log.select(start=1, limit=10)
    notifications_sorted = sorted(notifications, key=lambda n: n.id)

    # Expected topics in the order they should appear
    expected_topics = [
        'biomero.eventsourcing:WorkflowRun.WorkflowInitiated',
        'biomero.eventsourcing:WorkflowRun.WorkflowFailed'
    ]

    # Verify the order of notification topics
    actual_topics = [n.topic for n in notifications_sorted]
    assert actual_topics == expected_topics, (
        f"Expected topics order: {expected_topics}, but got: {actual_topics}"
    )

    # Verify the failure notification contains the correct error message
    failure_notification = next(
        (n for n in notifications_sorted if n.topic ==
         'biomero.eventsourcing:WorkflowRun.WorkflowFailed'),
        None
    )
    assert failure_notification is not None, "Expected a WorkflowFailed notification"

    # Decode the state to verify the error message
    state_str = failure_notification.state.decode('utf-8')
    assert '"error_message":"Some error"' in state_str, (
        f"Expected 'error_message: Some error', but got: {state_str}"
    )


def test_complete_task(workflow_tracker):
    # GIVEN a workflow with a task
    workflow_id = workflow_tracker.initiate_workflow(
        name="Workflow with Task",
        description="Description of workflow",
        user=1,
        group=1
    )
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id=workflow_id,
        task_name="Task 1",
        task_version="1.0",
        input_data={"input_key": "input_value"},
        kwargs={}
    )

    # WHEN the task is started and then completed
    workflow_tracker.start_task(task_id)
    workflow_tracker.complete_task(
        task_id, message="Task completed successfully")

    # THEN verify the task completion notification
    notifications = workflow_tracker.notification_log.select(start=1, limit=10)
    notifications_sorted = sorted(notifications, key=lambda n: n.id)

    # Expected topics in the order they should appear
    expected_topics = [
        'biomero.eventsourcing:WorkflowRun.WorkflowInitiated',
        'biomero.eventsourcing:Task.TaskCreated',
        'biomero.eventsourcing:WorkflowRun.TaskAdded',
        'biomero.eventsourcing:Task.TaskStarted',
        'biomero.eventsourcing:Task.TaskCompleted'
    ]

    # Verify the order of notification topics
    actual_topics = [n.topic for n in notifications_sorted]
    assert actual_topics == expected_topics, (
        f"Expected topics order: {expected_topics}, but got: {actual_topics}"
    )

    # THEN Verify the task is marked as completed
    task = workflow_tracker.repository.get(task_id)
    assert task.result_message == "Task completed successfully"


def test_fail_task(workflow_tracker):
    # GIVEN a workflow with a task
    workflow_id = workflow_tracker.initiate_workflow(
        name="Workflow with Task",
        description="Description of workflow",
        user=1,
        group=1
    )
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id=workflow_id,
        task_name="Task 1",
        task_version="1.0",
        input_data={"input_key": "input_value"},
        kwargs={}
    )

    # WHEN the task is failed
    workflow_tracker.fail_task(
        task_id, error_message="Task failed due to an error")

    # THEN verify the task failure notification
    notifications = workflow_tracker.notification_log.select(start=1, limit=10)
    notifications_sorted = sorted(notifications, key=lambda n: n.id)

    # Expected topics in the order they should appear
    expected_topics = [
        'biomero.eventsourcing:WorkflowRun.WorkflowInitiated',
        'biomero.eventsourcing:Task.TaskCreated',
        'biomero.eventsourcing:WorkflowRun.TaskAdded',
        'biomero.eventsourcing:Task.TaskFailed'
    ]

    # Verify the order of notification topics
    actual_topics = [n.topic for n in notifications_sorted]
    assert actual_topics == expected_topics, (
        f"Expected topics order: {expected_topics}, but got: {actual_topics}"
    )

    # THEN Verify the task is marked as failed
    task: Task = workflow_tracker.repository.get(task_id)
    assert task.result_message == "Task failed due to an error"


def test_add_job_id(workflow_tracker):
    # GIVEN a workflow with a task
    workflow_id = workflow_tracker.initiate_workflow(
        name="Workflow with Task",
        description="Description of workflow",
        user=1,
        group=1
    )
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id=workflow_id,
        task_name="Task 1",
        task_version="1.0",
        input_data={"input_key": "input_value"},
        kwargs={}
    )

    # WHEN a job ID is added to the task
    job_id = "12345"
    workflow_tracker.add_job_id(task_id, slurm_job_id=job_id)

    # THEN verify the job ID addition notification
    notifications = workflow_tracker.notification_log.select(start=1, limit=10)
    notifications_sorted = sorted(notifications, key=lambda n: n.id)

    # Expected topics in the order they should appear
    expected_topics = [
        'biomero.eventsourcing:WorkflowRun.WorkflowInitiated',
        'biomero.eventsourcing:Task.TaskCreated',
        'biomero.eventsourcing:WorkflowRun.TaskAdded',
        'biomero.eventsourcing:Task.JobIdAdded'
    ]

    # Verify the order of notification topics
    actual_topics = [n.topic for n in notifications_sorted]
    assert actual_topics == expected_topics, (
        f"Expected topics order: {expected_topics}, but got: {actual_topics}"
    )

    # THEN Verify the job ID is added correctly
    task = workflow_tracker.repository.get(task_id)
    assert task.job_ids == [job_id]


def test_add_result(workflow_tracker):
    # GIVEN a workflow with a task
    workflow_id = workflow_tracker.initiate_workflow(
        name="Workflow with Task",
        description="Description of workflow",
        user=1,
        group=1
    )
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id=workflow_id,
        task_name="Task 1",
        task_version="1.0",
        input_data={"input_key": "input_value"},
        kwargs={}
    )
    expected_result = {
        "command": "lss -lah",
        "env": "MY_PASS=SECRET",
        "stdout": "\n",
        "stderr": "oops did you mean ls?"
    }

    # Mock the Result object from the fabric library
    with patch('fabric.Result') as MockResult:
        # Create a mock instance with specific return values
        mock_result = MockResult.return_value
        mock_result.command = "lss -lah"
        mock_result.env = "MY_PASS=SECRET"
        mock_result.stdout = "\n"
        mock_result.stderr = "oops did you mean ls?"

        # WHEN a result is added to the task
        workflow_tracker.add_result(task_id, mock_result)

    # THEN verify the result addition notification
    notifications = workflow_tracker.notification_log.select(start=1, limit=10)
    notifications_sorted = sorted(notifications, key=lambda n: n.id)

    # Expected topics in the order they should appear
    expected_topics = [
        'biomero.eventsourcing:WorkflowRun.WorkflowInitiated',
        'biomero.eventsourcing:Task.TaskCreated',
        'biomero.eventsourcing:WorkflowRun.TaskAdded',
        'biomero.eventsourcing:Task.ResultAdded'
    ]

    # Verify the order of notification topics
    actual_topics = [n.topic for n in notifications_sorted]
    assert actual_topics == expected_topics, (
        f"Expected topics order: {expected_topics}, but got: {actual_topics}"
    )

    # THEN Verify the result is added correctly
    task = workflow_tracker.repository.get(task_id)
    assert task.results == [expected_result]


def test_update_task_status(workflow_tracker):
    # GIVEN a workflow with a task
    workflow_id = workflow_tracker.initiate_workflow(
        name="Workflow with Task",
        description="Description of workflow",
        user=1,
        group=1
    )
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id=workflow_id,
        task_name="Task 1",
        task_version="1.0",
        input_data={"input_key": "input_value"},
        kwargs={}
    )

    # WHEN the task status is updated
    workflow_tracker.update_task_status(task_id, status="In Progress")

    # THEN verify the task status update notification
    notifications = workflow_tracker.notification_log.select(start=1, limit=10)
    notifications_sorted = sorted(notifications, key=lambda n: n.id)

    # Expected topics in the order they should appear
    expected_topics = [
        'biomero.eventsourcing:WorkflowRun.WorkflowInitiated',
        'biomero.eventsourcing:Task.TaskCreated',
        'biomero.eventsourcing:WorkflowRun.TaskAdded',
        'biomero.eventsourcing:Task.StatusUpdated'
    ]

    # Verify the order of notification topics
    actual_topics = [n.topic for n in notifications_sorted]
    assert actual_topics == expected_topics, (
        f"Expected topics order: {expected_topics}, but got: {actual_topics}"
    )

    # THEN Verify the status is updated correctly
    task = workflow_tracker.repository.get(task_id)
    assert task.status == "In Progress"


def test_update_task_progress(workflow_tracker):
    # GIVEN a workflow with a task
    workflow_id = workflow_tracker.initiate_workflow(
        name="Workflow with Task",
        description="Description of workflow",
        user=1,
        group=1
    )
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id=workflow_id,
        task_name="Task 1",
        task_version="1.0",
        input_data={"input_key": "input_value"},
        kwargs={}
    )

    # WHEN the task progress is updated
    workflow_tracker.update_task_progress(task_id, progress="50%")

    # THEN verify the task progress update notification
    notifications = workflow_tracker.notification_log.select(start=1, limit=10)
    notifications_sorted = sorted(notifications, key=lambda n: n.id)

    # Expected topics in the order they should appear
    expected_topics = [
        'biomero.eventsourcing:WorkflowRun.WorkflowInitiated',
        'biomero.eventsourcing:Task.TaskCreated',
        'biomero.eventsourcing:WorkflowRun.TaskAdded',
        'biomero.eventsourcing:Task.ProgressUpdated'
    ]

    # Verify the order of notification topics
    actual_topics = [n.topic for n in notifications_sorted]
    assert actual_topics == expected_topics, (
        f"Expected topics order: {expected_topics}, but got: {actual_topics}"
    )

    # THEN Verify the progress is updated correctly
    task = workflow_tracker.repository.get(task_id)
    assert task.progress == "50%"


def test_job_acc_workflow_initiated(workflow_tracker_and_job_accounting):
    # GIVEN a WorkflowTracker event system and job accounting listener
    workflow_tracker: WorkflowTracker
    job_accounting: JobAccounting
    workflow_tracker, job_accounting = workflow_tracker_and_job_accounting

    # WHEN a new workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow("Test Workflow",
                                                     "Test Description",
                                                     user=1,
                                                     group=2)

    # THEN verify internal state in JobAccounting
    assert workflow_id in job_accounting.workflows
    assert job_accounting.workflows[workflow_id] == {"user": 1, "group": 2}


def test_job_acc_task_added(workflow_tracker_and_job_accounting):
    # GIVEN a WorkflowTracker event system and job accounting listener
    workflow_tracker: WorkflowTracker
    job_accounting: JobAccounting
    workflow_tracker, job_accounting = workflow_tracker_and_job_accounting

    # WHEN a new workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow("Test Workflow",
                                                     "Test Description",
                                                     user=1,
                                                     group=2)
    # And a task is added
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id=workflow_id,
        task_name="task",
        task_version="v1",
        input_data={"foo": "bar"},
        kwargs={"bar": "baz"}
    )

    # THEN verify internal state in JobAccounting
    assert workflow_id in job_accounting.workflows
    assert job_accounting.workflows[workflow_id] == {"user": 1, "group": 2}
    assert job_accounting.tasks[task_id] == workflow_id


def test_job_acc_job_id_added(workflow_tracker_and_job_accounting):
    # GIVEN a WorkflowTracker event system and job accounting listener
    workflow_tracker: WorkflowTracker
    job_accounting: JobAccounting
    workflow_tracker, job_accounting = workflow_tracker_and_job_accounting

    # WHEN a workflow is initiated and a task is added
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})

    # Add a job ID to the task
    job_id = "12345"
    workflow_tracker.add_job_id(task_id, job_id)

    # THEN verify internal state in JobAccounting
    assert job_id in job_accounting.jobs
    assert job_accounting.jobs[job_id] == (task_id, 1, 2)

    # Verify the JobView entry using SQLAlchemy
    with EngineManager.get_session() as session:
        job_view_entry = session.query(JobView).filter_by(
            slurm_job_id=job_id).first()
        assert job_view_entry is not None
        # Assuming job_id is stored as an integer
        assert job_view_entry.slurm_job_id == int(job_id)
        assert job_view_entry.user == 1
        assert job_view_entry.group == 2


def test_job_acc_update_view_table(workflow_tracker_and_job_accounting):
    # GIVEN a WorkflowTracker event system and job accounting listener
    workflow_tracker: WorkflowTracker
    job_accounting: JobAccounting
    workflow_tracker, job_accounting = workflow_tracker_and_job_accounting

    # WHEN updating the view table with job information
    job_id = "67890"
    user = 1
    group = 2
    job_accounting.update_view_table(job_id=job_id, user=user, group=group, 
                                     task_id=uuid.uuid4())

    # THEN verify the JobView entry using SQLAlchemy
    with EngineManager.get_session() as session:
        job_view_entry = session.query(JobView).filter_by(
            slurm_job_id=int(job_id)).first()
        assert job_view_entry is not None
        assert job_view_entry.slurm_job_id == int(job_id)
        assert job_view_entry.user == user
        assert job_view_entry.group == group


def test_job_acc_get_jobs_for_user(workflow_tracker_and_job_accounting):
    # GIVEN a WorkflowTracker event system and job accounting listener
    workflow_tracker: WorkflowTracker
    job_accounting: JobAccounting
    workflow_tracker, job_accounting = workflow_tracker_and_job_accounting

    # Simulate adding jobs
    job_accounting.update_view_table(job_id=100, user=1, group=2, 
                                     task_id=uuid.uuid4())
    job_accounting.update_view_table(job_id=200, user=1, group=2, 
                                     task_id=uuid.uuid4())
    job_accounting.update_view_table(job_id=300, user=2, group=3, 
                                     task_id=uuid.uuid4())

    # WHEN retrieving jobs for a specific user
    jobs = job_accounting.get_jobs(user=1)

    # THEN verify the jobs are correctly retrieved
    assert jobs == {1: [100, 200]}

    # Verify JobView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_views = session.query(JobView).filter(JobView.user == 1).all()
        job_ids = [job_view.slurm_job_id for job_view in job_views]
        assert set(job_ids) == {100, 200}


def test_job_acc_get_jobs_for_group(workflow_tracker_and_job_accounting):
    # GIVEN a WorkflowTracker event system and job accounting listener
    workflow_tracker: WorkflowTracker
    job_accounting: JobAccounting
    workflow_tracker, job_accounting = workflow_tracker_and_job_accounting

    # Simulate adding jobs
    job_accounting.update_view_table(job_id=400, user=1, group=2,
                                     task_id=uuid.uuid4())
    job_accounting.update_view_table(job_id=500, user=2, group=2,
                                     task_id=uuid.uuid4())
    job_accounting.update_view_table(job_id=600, user=2, group=3,
                                     task_id=uuid.uuid4())

    # WHEN retrieving jobs for a specific group
    jobs = job_accounting.get_jobs(group=2)

    # THEN verify the jobs are correctly retrieved
    assert jobs == {None: [400, 500]}

    # Verify JobView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_views = session.query(JobView).filter(JobView.group == 2).all()
        job_ids = [job_view.slurm_job_id for job_view in job_views]
        assert set(job_ids) == {400, 500}


def test_job_acc_get_jobs_all(workflow_tracker_and_job_accounting):
    # GIVEN a WorkflowTracker event system and job accounting listener
    workflow_tracker: WorkflowTracker
    job_accounting: JobAccounting
    workflow_tracker, job_accounting = workflow_tracker_and_job_accounting

    # Simulate adding jobs
    job_accounting.update_view_table(job_id=700, user=1, group=2,
                                     task_id=uuid.uuid4())
    job_accounting.update_view_table(job_id=800, user=1, group=2,
                                     task_id=uuid.uuid4())
    job_accounting.update_view_table(job_id=900, user=2, group=3,
                                     task_id=uuid.uuid4())

    # WHEN retrieving all jobs
    jobs = job_accounting.get_jobs()

    # THEN verify the jobs are correctly grouped by user
    assert jobs == {1: [700, 800], 2: [900]}

    # Verify JobView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_views = session.query(JobView).all()
        user_jobs = {}
        for job_view in job_views:
            if job_view.user not in user_jobs:
                user_jobs[job_view.user] = []
            user_jobs[job_view.user].append(job_view.slurm_job_id)
        assert user_jobs == {1: [700, 800], 2: [900]}


def test_workflow_progress_workflow_initiated(workflow_tracker_and_workflow_progress):
    # GIVEN a WorkflowTracker event system and workflow progress listener
    workflow_tracker: WorkflowTracker
    workflow_progress: WorkflowProgress
    workflow_tracker, workflow_progress = workflow_tracker_and_workflow_progress

    # WHEN a workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)

    # THEN verify internal state in WorkflowProgress
    assert workflow_id in workflow_progress.workflows
    assert workflow_progress.workflows[workflow_id]["status"] == wfs.INITIALIZING
    assert workflow_progress.workflows[workflow_id]["progress"] == "0%"
    assert workflow_progress.workflows[workflow_id]["user"] == 1
    assert workflow_progress.workflows[workflow_id]["group"] == 2


def test_workflow_progress_workflow_completed(workflow_tracker_and_workflow_progress):
    # GIVEN a WorkflowTracker event system and workflow progress listener
    workflow_tracker: WorkflowTracker
    workflow_progress: WorkflowProgress
    workflow_tracker, workflow_progress = workflow_tracker_and_workflow_progress

    # WHEN a workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)

    # Complete the workflow
    workflow_tracker.complete_workflow(workflow_id)

    # THEN verify internal state in WorkflowProgress
    assert workflow_id in workflow_progress.workflows
    assert workflow_progress.workflows[workflow_id]["status"] == wfs.DONE
    assert workflow_progress.workflows[workflow_id]["progress"] == "100%"


def test_workflow_progress_workflow_failed(workflow_tracker_and_workflow_progress):
    # GIVEN a WorkflowTracker event system and workflow progress listener
    workflow_tracker: WorkflowTracker
    workflow_progress: WorkflowProgress
    workflow_tracker, workflow_progress = workflow_tracker_and_workflow_progress

    # WHEN a workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)

    # Mark the workflow as failed
    error_message = "An error occurred"
    workflow_tracker.fail_workflow(workflow_id, error_message)

    # THEN verify internal state in WorkflowProgress
    assert workflow_id in workflow_progress.workflows
    assert workflow_progress.workflows[workflow_id]["status"] == wfs.FAILED


def test_workflow_progress_task_added(workflow_tracker_and_workflow_progress):
    # GIVEN a WorkflowTracker event system and workflow progress listener
    workflow_tracker: WorkflowTracker
    workflow_progress: WorkflowProgress
    workflow_tracker, workflow_progress = workflow_tracker_and_workflow_progress

    # WHEN a workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)

    # Add a task to the workflow
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})

    # THEN verify internal state in WorkflowProgress
    assert task_id in workflow_progress.tasks
    assert workflow_progress.tasks[task_id]["workflow_id"] == workflow_id
    assert workflow_progress.workflows[workflow_id]["task"] == "task1"


def test_workflow_progress_task_status_updated(workflow_tracker_and_workflow_progress):
    # GIVEN a WorkflowTracker event system and workflow progress listener
    workflow_tracker: WorkflowTracker
    workflow_progress: WorkflowProgress
    workflow_tracker, workflow_progress = workflow_tracker_and_workflow_progress

    # WHEN a workflow is initiated and a task is added
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})

    # Update the task status
    status = "InProgress"
    workflow_tracker.update_task_status(task_id, status)

    # THEN verify internal state in WorkflowProgress
    assert task_id in workflow_progress.tasks
    assert workflow_progress.tasks[task_id]["workflow_id"] == workflow_id
    assert workflow_progress.workflows[workflow_id]["status"] == wfs.JOB_STATUS + status


def test_workflow_progress_task_progress_updated(workflow_tracker_and_workflow_progress):
    # GIVEN a WorkflowTracker event system and workflow progress listener
    workflow_tracker: WorkflowTracker
    workflow_progress: WorkflowProgress
    workflow_tracker, workflow_progress = workflow_tracker_and_workflow_progress

    # WHEN a workflow is initiated and a task is added
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})

    # Update the task progress
    progress = "25%"
    workflow_tracker.update_task_progress(task_id, progress)

    # THEN verify internal state in WorkflowProgress
    assert workflow_progress.tasks[task_id]["progress"] == progress
    assert workflow_progress.workflows[workflow_id]["task_progress"] == progress
    
   
def test_workflow_progress_all_statuses(workflow_tracker_and_workflow_progress):
    # GIVEN a WorkflowTracker event system and workflow progress listener
    workflow_tracker: WorkflowTracker
    workflow_progress: WorkflowProgress
    workflow_tracker, workflow_progress = workflow_tracker_and_workflow_progress

    # WHEN a workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)

    # Add tasks with names that will trigger all branches
    task_names = [
        ('_SLURM_Image_Transfer.py', 'InProgress'),
        ('convert_image', 'InProgress'),  # should match the convert_ condition
        ('SLURM_Get_Results.py', 'InProgress'),
        ('SLURM_Run_Workflow.py', 'InProgress'),
        ('unknown_task', 'InProgress')
    ]

    for task_name, status in task_names:
        task_id = workflow_tracker.add_task_to_workflow(workflow_id, task_name, "v1", {"foo": "bar"}, {"bar": "baz"})
        workflow_tracker.update_task_status(task_id, status)  # Update status to trigger logic

        # Check expected status and progress after each update
        if task_name == '_SLURM_Image_Transfer.py':
            expected_status = wfs.TRANSFERRING
            expected_progress = "5%"
        elif task_name.startswith('convert_'):
            expected_status = wfs.CONVERTING
            expected_progress = "25%"
        elif task_name == 'SLURM_Get_Results.py':
            expected_status = wfs.RETRIEVING
            expected_progress = "90%"
        elif task_name == 'SLURM_Run_Workflow.py':
            expected_status = wfs.RUNNING
            expected_progress = "50%"
        else:
            expected_status = wfs.JOB_STATUS + status
            expected_progress = "50%"

        # Validate after each task status update
        assert workflow_progress.workflows[workflow_id]["status"] == expected_status
        assert workflow_progress.workflows[workflow_id]["progress"] == expected_progress

    # Introduce task progress for interpolation
    # Assume a task that updates its progress
    task_id = workflow_tracker.add_task_to_workflow(workflow_id, 'some_task', "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_tracker.update_task_progress(task_id, "43%")  # Simulate a progress update of 43%
    
    # Trigger the status update for the last task
    workflow_tracker.update_task_status(task_id, 'InProgress')

    # Check the workflow's updated progress using interpolation
    expected_interpolated_progress = "67.2%"

    # Assert final workflow status and interpolated progress
    assert workflow_progress.workflows[workflow_id]["status"] == wfs.JOB_STATUS + 'InProgress'  # Final expected status
    assert workflow_progress.workflows[workflow_id]["progress"] == expected_interpolated_progress  # Check interpolated progress


def test_job_progress_job_id_added(workflow_tracker_and_job_progress):
    # GIVEN a WorkflowTracker event system and job progress listener
    workflow_tracker: WorkflowTracker
    job_progress: JobProgress
    workflow_tracker, job_progress = workflow_tracker_and_job_progress

    # WHEN a workflow is initiated and a task is added
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})

    # Add a job ID to the task
    job_id = "12345"
    workflow_tracker.add_job_id(task_id, job_id)

    # THEN verify internal state in JobProgress
    assert task_id in job_progress.task_to_job
    assert job_progress.task_to_job[task_id] == job_id

    # Verify JobProgressView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_progress_views = session.query(JobProgressView).filter(
            JobProgressView.slurm_job_id == job_id).all()
        assert len(job_progress_views) == 0


def test_job_progress_status_updated(workflow_tracker_and_job_progress):
    # GIVEN a WorkflowTracker event system and job progress listener
    workflow_tracker: WorkflowTracker
    job_progress: JobProgress
    workflow_tracker, job_progress = workflow_tracker_and_job_progress

    # WHEN a workflow is initiated and a task is added
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})

    # Add a job ID to the task
    job_id = 200
    workflow_tracker.add_job_id(task_id, job_id)

    # Update the task status
    status = "InProgress"
    workflow_tracker.update_task_status(task_id, status)

    # THEN verify internal state in JobProgress
    assert job_id in job_progress.job_status
    assert job_progress.job_status[job_id]["status"] == status

    # Verify JobProgressView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_progress_views = session.query(JobProgressView).filter(
            JobProgressView.slurm_job_id == job_id).all()
        assert len(job_progress_views) == 1  # Expecting exactly one entry
        job_progress_view = job_progress_views[0]
        assert job_progress_view.slurm_job_id == job_id
        assert job_progress_view.status == status
        assert job_progress_view.progress is None  # No progress set in this test
        
    # Update the task status
    status = "COMPLETED"
    workflow_tracker.update_task_status(task_id, status)

    # THEN verify internal state in JobProgress
    assert job_id in job_progress.job_status
    assert job_progress.job_status[job_id]["status"] == status

    # Verify JobProgressView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_progress_views = session.query(JobProgressView).filter(
            JobProgressView.slurm_job_id == job_id).all()
        assert len(job_progress_views) == 1  # Expecting exactly one entry
        job_progress_view = job_progress_views[0]
        assert job_progress_view.slurm_job_id == job_id
        assert job_progress_view.status == status
        assert job_progress_view.progress is None  # No progress set in this test


def test_job_progress_progress_updated(workflow_tracker_and_job_progress):
    # GIVEN a WorkflowTracker event system and job progress listener
    workflow_tracker: WorkflowTracker
    job_progress: JobProgress
    workflow_tracker, job_progress = workflow_tracker_and_job_progress

    # WHEN a workflow is initiated and a task is added
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})

    # Add a job ID to the task
    job_id = 12345  # Use a simple integer for the job ID
    workflow_tracker.add_job_id(task_id, job_id)

    # Update the task progress
    progress = "50%"
    workflow_tracker.update_task_progress(task_id, progress)

    # THEN verify internal state in JobProgress
    assert job_id in job_progress.job_status
    assert job_progress.job_status[job_id]["progress"] == progress

    # Verify JobProgressView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_progress_views = session.query(JobProgressView).filter(
            JobProgressView.slurm_job_id == job_id).all()
        assert len(job_progress_views) == 1
        job_progress_view = job_progress_views[0]
        assert job_progress_view.slurm_job_id == job_id
        assert job_progress_view.status == "UNKNOWN"
        assert job_progress_view.progress == progress
        
    # Update the task progress
    progress = "100%"
    workflow_tracker.update_task_progress(task_id, progress)

    # THEN verify internal state in JobProgress
    assert job_id in job_progress.job_status
    assert job_progress.job_status[job_id]["progress"] == progress

    # Verify JobProgressView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_progress_views = session.query(JobProgressView).filter(
            JobProgressView.slurm_job_id == job_id).all()
        assert len(job_progress_views) == 1
        job_progress_view = job_progress_views[0]
        assert job_progress_view.slurm_job_id == job_id
        assert job_progress_view.status == "UNKNOWN"
        assert job_progress_view.progress == progress


def test_job_progress_update_view_table(workflow_tracker_and_job_progress):
    # GIVEN a WorkflowTracker event system and job progress listener
    workflow_tracker: WorkflowTracker
    job_progress: JobProgress
    workflow_tracker, job_progress = workflow_tracker_and_job_progress

    # WHEN a job status is set
    job_id = 12345
    job_progress.job_status[job_id] = {"status": "RUNNING", "progress": "50%"}

    # Force update to the view table
    job_progress.update_view_table(job_id)

    # THEN verify JobProgressView entries using SQLAlchemy
    with EngineManager.get_session() as session:
        job_progress_view = session.query(JobProgressView).filter(
            JobProgressView.slurm_job_id == job_id).one_or_none()
        assert job_progress_view is not None
        assert job_progress_view.slurm_job_id == job_id
        assert job_progress_view.status == "RUNNING"
        assert job_progress_view.progress == "50%"


def test_job_progress_update_view_table_failed(workflow_tracker_and_job_progress, caplog):
    # GIVEN a WorkflowTracker event system and job progress listener
    workflow_tracker: WorkflowTracker
    job_progress: JobProgress
    workflow_tracker, job_progress = workflow_tracker_and_job_progress

    # WHEN a job status is not set
    job_id = 12345
    job_progress.job_status[job_id] = {"status": None, "progress": "50%"}

    # Force update to the view table
    with caplog.at_level("ERROR"):
        job_progress.update_view_table(job_id)
    
    # THEN
    assert f"Failed to insert/update job progress in view table: job_id={job_id}" in caplog.text

  
def test_wfanalytics_workflow_initiated(workflow_tracker_and_workflow_analytics):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # WHEN a workflow is initiated
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)

    # THEN verify internal state in WorkflowAnalytics
    assert workflow_id in workflow_analytics.workflows
    assert workflow_analytics.workflows[workflow_id] == {"user": 1, "group": 2}


def test_wfanalytics_task_added(workflow_tracker_and_workflow_analytics, caplog):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # WHEN a workflow is initiated and a task is added
    before = datetime.now(timezone.utc)
    with caplog.at_level("DEBUG"):
        workflow_id = workflow_tracker.initiate_workflow(
            "Test Workflow", "Test Description", user=1, group=2)
        task_id = workflow_tracker.add_task_to_workflow(
            workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})  # both

    # THEN verify internal state in WorkflowAnalytics
    assert task_id in workflow_analytics.tasks
    task_info = workflow_analytics.tasks[task_id]
    assert task_info["wf_id"] == workflow_id
    assert task_info["task_name"] == "task"
    assert task_info["task_version"] == "v1"
    assert task_info["status"] == "CREATED"
    now = datetime.now(timezone.utc)
    assert task_info["start_time"] >= before
    assert task_info["start_time"] <= now
    wf_info = workflow_analytics.workflows[workflow_id]
    assert wf_info["user"] == 1
    assert wf_info["group"] == 2
    wf_id = workflow_analytics.tasks.get(task_id).get("wf_id")
    assert wf_id == workflow_id
    assert wf_id in workflow_analytics.workflows
    assert workflow_analytics.workflows[wf_id]["user"] == 1

    # THEN check logs for WorkflowInitiated event
    assert f"Workflow initiated: wf_id={workflow_id}, user=1, group=2" in caplog.text

    # THEN check logs for TaskAdded event
    assert f"Task added: task_id={task_id}, wf_id={workflow_id}" in caplog.text

    # THEN check logs for TaskCreated event
    assert f"Task created: task_id={task_id}, task_name=task, timestamp=" in caplog.text


def test_wfanalytics_task_created(workflow_tracker_and_workflow_analytics):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # WHEN a workflow is initiated and a task is created
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_tracker.update_task_progress(task_id, "CREATED")

    # THEN verify internal state in WorkflowAnalytics
    assert task_id in workflow_analytics.tasks
    task_info = workflow_analytics.tasks[task_id]
    assert task_info["wf_id"] == workflow_id
    assert task_info["task_name"] == "task"
    assert task_info["task_version"] == "v1"
    assert task_info["status"] == "CREATED"
    assert task_info["start_time"] is not None
    wf_info = workflow_analytics.workflows[workflow_id]
    assert wf_info["user"] == 1
    assert wf_info["group"] == 2
    wf_id = workflow_analytics.tasks.get(task_id).get("wf_id")
    assert wf_id == workflow_id
    assert wf_id in workflow_analytics.workflows
    assert workflow_analytics.workflows[wf_id]["user"] == 1
    
    
def test_wfanalytics_task_completed(workflow_tracker_and_workflow_analytics, caplog):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # WHEN a workflow is initiated and a task is completed
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})
    with caplog.at_level("DEBUG"):
        workflow_tracker.complete_task(task_id, "done")

    # THEN verify internal state in WorkflowAnalytics
    assert task_id in workflow_analytics.tasks
    task_info = workflow_analytics.tasks[task_id]
    assert task_info["wf_id"] == workflow_id
    assert task_info["task_name"] == "task"
    assert task_info["task_version"] == "v1"
    assert task_info["status"] == "CREATED"
    assert task_info["start_time"] is not None
    
    # Verify TaskExecution entries using SQLAlchemy
    with EngineManager.get_session() as session:
        task_execution: TaskExecution = session.query(TaskExecution).filter_by(
            task_id=task_id).first()
        assert task_execution is not None
        assert task_execution.task_id == task_id
        assert task_execution.task_name == "task"
        assert task_execution.task_version == "v1"
        assert task_execution.status == "CREATED"
        assert task_execution.start_time is not None
        assert task_execution.end_time is not None
        assert task_execution.user_id == 1
        assert task_execution.group_id == 2
        
    # THEN
    completed = task_info["end_time"]
    assert f"Task completed: task_id={task_id}, end_time={completed}" in caplog.text
    
def test_wfanalytics_task_failed(workflow_tracker_and_workflow_analytics, caplog):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # WHEN a workflow is initiated and a task is completed
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})
    with caplog.at_level("DEBUG"):
        error_message = "failed"
        workflow_tracker.fail_task(task_id, error_message)

    # THEN verify internal state in WorkflowAnalytics
    assert task_id in workflow_analytics.tasks
    task_info = workflow_analytics.tasks[task_id]
    assert task_info["wf_id"] == workflow_id
    assert task_info["task_name"] == "task"
    assert task_info["task_version"] == "v1"
    assert task_info["status"] == "CREATED"
    assert task_info["start_time"] is not None
    assert task_info["end_time"] is not None
    assert task_info["error_type"] == error_message
    
    # Verify TaskExecution entries using SQLAlchemy
    with EngineManager.get_session() as session:
        task_execution: TaskExecution = session.query(TaskExecution).filter_by(
            task_id=task_id).first()
        assert task_execution is not None
        assert task_execution.task_id == task_id
        assert task_execution.task_name == "task"
        assert task_execution.task_version == "v1"
        assert task_execution.status == "CREATED"
        assert task_execution.start_time is not None
        assert task_execution.end_time is not None
        assert task_execution.error_type == error_message
        assert task_execution.user_id == 1
        assert task_execution.group_id == 2
        
    # THEN
    t_failed = task_info["end_time"]
    assert f"Task failed: task_id={task_id}, end_time={t_failed}, error={error_message}" in caplog.text


def test_wfanalytics_update_view_table(workflow_tracker_and_workflow_analytics, caplog):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # WHEN a workflow is initiated and a task is created
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)
    task_id = workflow_tracker.add_task_to_workflow(
        workflow_id, "task", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_tracker.update_task_progress(task_id, "CREATED")

    # Verify TaskExecution entries using SQLAlchemy
    with EngineManager.get_session() as session:
        task_execution: TaskExecution = session.query(TaskExecution).filter_by(
            task_id=task_id).first()
        assert task_execution is not None
        assert task_execution.task_id == task_id
        assert task_execution.task_name == "task"
        assert task_execution.task_version == "v1"
        assert task_execution.status == "CREATED"
        assert task_execution.start_time is not None
        assert task_execution.user_id is None  # No update sent to DB yet
        assert task_execution.group_id is None  # No update sent to DB yet

    # Verify that the entry was added to the SQLAlchemy table
    workflow_analytics.update_view_table(task_id)

    with EngineManager.get_session() as session:
        # Ensure the new task is inserted
        task_execution: TaskExecution = session.query(
            TaskExecution).filter_by(task_id=task_id).first()
        assert task_execution is not None
        assert task_execution.task_id == task_id
        assert task_execution.task_name == "task"
        assert task_execution.task_version == "v1"
        assert task_execution.status == "CREATED"
        assert task_execution.start_time is not None
        assert task_execution.user_id == 1
        assert task_execution.group_id == 2

    # Update the task status
    workflow_tracker.update_task_status(task_id, "RUNNING")
    workflow_analytics.tasks[task_id]["status"] = "RUNNING"
    workflow_analytics.update_view_table(task_id)

    with EngineManager.get_session() as session:
        # Ensure the status was updated
        task_execution: TaskExecution = session.query(
            TaskExecution).filter_by(task_id=task_id).first()
        assert task_execution is not None
        assert task_execution.status == "RUNNING"

    # Update the task's name and version
    workflow_analytics.tasks[task_id]["task_name"] = "updated_task"
    workflow_analytics.tasks[task_id]["task_version"] = "v2"
    workflow_analytics.update_view_table(task_id)

    with EngineManager.get_session() as session:
        # Ensure the task name and version were updated
        task_execution: TaskExecution = session.query(
            TaskExecution).filter_by(task_id=task_id).first()
        assert task_execution is not None
        assert task_execution.task_name == "updated_task"
        assert task_execution.task_version == "v2"

    # Simulate the case where end_time is updated
    end_time = datetime.now()
    workflow_analytics.tasks[task_id]["end_time"] = end_time
    workflow_analytics.update_view_table(task_id)

    with EngineManager.get_session() as session:
        # Ensure the end_time was updated
        task_execution: TaskExecution = session.query(
            TaskExecution).filter_by(task_id=task_id).first()
        assert task_execution is not None
        assert task_execution.end_time == end_time

    # Simulate error_type being added
    workflow_analytics.tasks[task_id]["error_type"] = "TaskError"
    workflow_analytics.update_view_table(task_id)

    with EngineManager.get_session() as session:
        # Ensure the error_type was updated
        task_execution: TaskExecution = session.query(
            TaskExecution).filter_by(task_id=task_id).first()
        assert task_execution is not None
        assert task_execution.error_type == "TaskError"
        
    # no-op
    task_id2 = uuid.uuid4()
    workflow_analytics.update_view_table(task_id2)
    with EngineManager.get_session() as session:
        # Ensure the end_time was updated
        task_execution: TaskExecution = session.query(
            TaskExecution).filter_by(task_id=task_id2).first()
        assert task_execution is None
        
        
    # rollback/integrityerror
    with caplog.at_level("ERROR"):
        workflow_analytics.tasks[task_id]["status"] = None
        workflow_analytics.update_view_table(task_id)
    
    assert f"Failed to insert/update task execution into view table: task_id={task_id}, error=" in caplog.text
    

def test_wfanalytics_get_task_counts_with_filters(workflow_tracker_and_workflow_analytics):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # GIVEN task data added to workflow_analytics.tasks for different users and groups
    workflow_id_1 = workflow_tracker.initiate_workflow(
        "Test Workflow 1", "Test Description 1", user=1, group=2)
    task_id1 = workflow_tracker.add_task_to_workflow(
        workflow_id_1, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    task_id2 = workflow_tracker.add_task_to_workflow(
        workflow_id_1, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    
    workflow_id_2 = workflow_tracker.initiate_workflow(
        "Test Workflow 2", "Test Description 2", user=2, group=2)
    task_id3 = workflow_tracker.add_task_to_workflow(
        workflow_id_2, "task2", "v1", {"foo": "bar"}, {"bar": "baz"})

    workflow_id_3 = workflow_tracker.initiate_workflow(
        "Test Workflow 3", "Test Description 3", user=1, group=3)
    task_id4 = workflow_tracker.add_task_to_workflow(
        workflow_id_3, "task3", "v1", {"foo": "bar"}, {"bar": "baz"})
    
    workflow_analytics.update_view_table(task_id1)
    workflow_analytics.update_view_table(task_id2)
    workflow_analytics.update_view_table(task_id3)
    workflow_analytics.update_view_table(task_id4)

    # WHEN calling get_task_counts without filters (should return counts for all tasks)
    result = workflow_analytics.get_task_counts()

    # THEN we should get the correct counts
    expected_result = {
        ("task1", "v1"): 2,  # Two tasks for workflow 1
        ("task2", "v1"): 1,  # One task for workflow 2
        ("task3", "v1"): 1   # One task for workflow 3
    }
    assert result == expected_result

    # WHEN calling get_task_counts filtered by user=1
    result_user_1 = workflow_analytics.get_task_counts(user=1)

    # THEN we should get the counts for tasks belonging to user 1 only
    expected_result_user_1 = {
        ("task1", "v1"): 2,  # Two tasks for user 1 (workflow 1)
        ("task3", "v1"): 1   # One task for user 1 (workflow 3)
    }
    assert result_user_1 == expected_result_user_1

    # WHEN calling get_task_counts filtered by group=2
    result_group_2 = workflow_analytics.get_task_counts(group=2)

    # THEN we should get the counts for tasks belonging to group 2 only
    expected_result_group_2 = {
        ("task1", "v1"): 2,  # Two tasks for group 2 (workflow 1)
        ("task2", "v1"): 1   # One task for group 2 (workflow 2)
    }
    assert result_group_2 == expected_result_group_2

    # WHEN calling get_task_counts filtered by both user=1 and group=2
    result_user_1_group_2 = workflow_analytics.get_task_counts(user=1, group=2)

    # THEN we should get the counts for tasks belonging to both user 1 and group 2 (workflow 1)
    expected_result_user_1_group_2 = {
        ("task1", "v1"): 2  # Two tasks for user 1 and group 2 (workflow 1)
    }
    assert result_user_1_group_2 == expected_result_user_1_group_2



def test_wfanalytics_get_average_task_duration(workflow_tracker_and_workflow_analytics):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # GIVEN task data added to workflow_analytics.tasks with start and end times
    workflow_id = workflow_tracker.initiate_workflow(
        "Test Workflow", "Test Description", user=1, group=2)

    # Simulating task creation with durations
    task_id_1 = workflow_tracker.add_task_to_workflow(
        workflow_id, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_1]["start_time"] = datetime(
        2023, 8, 1, 12, 0, 0)
    workflow_analytics.tasks[task_id_1]["end_time"] = datetime(
        2023, 8, 1, 12, 30, 0)  # 30 mins duration

    task_id_2 = workflow_tracker.add_task_to_workflow(
        workflow_id, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_2]["start_time"] = datetime(
        2023, 8, 1, 13, 0, 0)
    workflow_analytics.tasks[task_id_2]["end_time"] = datetime(
        2023, 8, 1, 13, 45, 0)  # 45 mins duration

    task_id_3 = workflow_tracker.add_task_to_workflow(
        workflow_id, "task2", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_3]["start_time"] = datetime(
        2023, 8, 1, 14, 0, 0)
    workflow_analytics.tasks[task_id_3]["end_time"] = datetime(
        2023, 8, 1, 14, 20, 0)  # 20 mins duration

    # Manually update the view table with the tasks' details
    workflow_analytics.update_view_table(task_id_1)
    workflow_analytics.update_view_table(task_id_2)
    workflow_analytics.update_view_table(task_id_3)

    # WHEN calling get_average_task_duration
    result = workflow_analytics.get_average_task_duration()

    # THEN we should get the correct average durations
    expected_result = {
        # Average of 30 and 45 minutes, converted to seconds
        ("task1", "v1"): (30 * 60 + 45 * 60) / 2,
        ("task2", "v1"): 20 * 60  # 20 minutes in seconds
    }
    assert result == expected_result


def test_wfanalytics_get_average_task_duration_with_filters(workflow_tracker_and_workflow_analytics):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # GIVEN task data added to workflow_analytics.tasks with start and end times for different users and groups
    workflow_id_1 = workflow_tracker.initiate_workflow(
        "Test Workflow 1", "Test Description", user=1, group=2)

    workflow_id_2 = workflow_tracker.initiate_workflow(
        "Test Workflow 2", "Test Description", user=3, group=4)

    # Simulating task creation with durations for user 1, group 2
    task_id_1 = workflow_tracker.add_task_to_workflow(
        workflow_id_1, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_1]["start_time"] = datetime(
        2023, 8, 1, 12, 0, 0)
    workflow_analytics.tasks[task_id_1]["end_time"] = datetime(
        2023, 8, 1, 12, 30, 0)  # 30 mins duration

    task_id_2 = workflow_tracker.add_task_to_workflow(
        workflow_id_1, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_2]["start_time"] = datetime(
        2023, 8, 1, 13, 0, 0)
    workflow_analytics.tasks[task_id_2]["end_time"] = datetime(
        2023, 8, 1, 13, 45, 0)  # 45 mins duration

    # Simulating task creation with durations for user 3, group 4
    task_id_3 = workflow_tracker.add_task_to_workflow(
        workflow_id_2, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_3]["start_time"] = datetime(
        2023, 8, 1, 14, 0, 0)
    workflow_analytics.tasks[task_id_3]["end_time"] = datetime(
        2023, 8, 1, 14, 30, 0)  # 30 mins duration

    task_id_4 = workflow_tracker.add_task_to_workflow(
        workflow_id_2, "task2", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_4]["start_time"] = datetime(
        2023, 8, 1, 15, 0, 0)
    workflow_analytics.tasks[task_id_4]["end_time"] = datetime(
        2023, 8, 1, 15, 20, 0)  # 20 mins duration

    # Manually update the view table with the tasks' details
    workflow_analytics.update_view_table(task_id_1)
    workflow_analytics.update_view_table(task_id_2)
    workflow_analytics.update_view_table(task_id_3)
    workflow_analytics.update_view_table(task_id_4)

    # WHEN calling get_average_task_duration without filters
    result_all = workflow_analytics.get_average_task_duration()

    # THEN we should get the correct average durations for all tasks
    expected_result_all = {
        # Average of 30, 45, and 30 minutes
        ("task1", "v1"): (30 * 60 + 45 * 60 + 30 * 60) / 3,
        ("task2", "v1"): 20 * 60  # 20 minutes in seconds
    }
    assert result_all == expected_result_all

    # WHEN calling get_average_task_duration filtered by user=1 (group=2 implicitly since user=1 only belongs to group=2)
    result_user_1 = workflow_analytics.get_average_task_duration(user=1)

    # THEN we should get the correct average durations for user 1's tasks
    expected_result_user_1 = {
        ("task1", "v1"): (30 * 60 + 45 * 60) / 2  # Average of 30 and 45 minutes
    }
    assert result_user_1 == expected_result_user_1

    # WHEN calling get_average_task_duration filtered by group=4 (tasks by user 3 in group 4)
    result_group_4 = workflow_analytics.get_average_task_duration(group=4)

    # THEN we should get the correct average durations for group 4's tasks
    expected_result_group_4 = {
        ("task1", "v1"): 30 * 60,  # 30 minutes
        ("task2", "v1"): 20 * 60   # 20 minutes
    }
    assert result_group_4 == expected_result_group_4

    # WHEN calling get_average_task_duration filtered by both user=3 and group=4
    result_user_3_group_4 = workflow_analytics.get_average_task_duration(
        user=3, group=4)

    # THEN we should get the correct average durations for user 3 in group 4
    expected_result_user_3_group_4 = {
        ("task1", "v1"): 30 * 60,  # 30 minutes
        ("task2", "v1"): 20 * 60   # 20 minutes
    }
    assert result_user_3_group_4 == expected_result_user_3_group_4
    
    
def test_wfanalytics_get_task_failures_with_filters(workflow_tracker_and_workflow_analytics):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # GIVEN task data added to workflow_analytics.tasks with failure reasons for different users and groups
    workflow_id_1 = workflow_tracker.initiate_workflow(
        "Test Workflow 1", "Test Description", user=1, group=2)

    workflow_id_2 = workflow_tracker.initiate_workflow(
        "Test Workflow 2", "Test Description", user=3, group=4)

    # Simulating task creation with failures for user 1, group 2
    task_id_1 = workflow_tracker.add_task_to_workflow(
        workflow_id_1, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_1]["error_type"] = "ErrorA"

    task_id_2 = workflow_tracker.add_task_to_workflow(
        workflow_id_1, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_2]["error_type"] = "ErrorB"

    # Simulating task creation with failures for user 3, group 4
    task_id_3 = workflow_tracker.add_task_to_workflow(
        workflow_id_2, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_3]["error_type"] = "ErrorC"

    task_id_4 = workflow_tracker.add_task_to_workflow(
        workflow_id_2, "task2", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_4]["error_type"] = "ErrorD"

    # Manually update the view table with the tasks' details
    workflow_analytics.update_view_table(task_id_1)
    workflow_analytics.update_view_table(task_id_2)
    workflow_analytics.update_view_table(task_id_3)
    workflow_analytics.update_view_table(task_id_4)

    # WHEN calling get_task_failures without filters
    result_all = workflow_analytics.get_task_failures()

    # THEN we should get the correct failures for all tasks
    expected_result_all = {
        ("task1", "v1"): ["ErrorA", "ErrorB", "ErrorC"],
        ("task2", "v1"): ["ErrorD"]
    }
    assert result_all == expected_result_all

    # WHEN calling get_task_failures filtered by user=1 (group=2 implicitly since user=1 only belongs to group=2)
    result_user_1 = workflow_analytics.get_task_failures(user=1)

    # THEN we should get the correct failures for user 1's tasks
    expected_result_user_1 = {
        ("task1", "v1"): ["ErrorA", "ErrorB"]
    }
    assert result_user_1 == expected_result_user_1

    # WHEN calling get_task_failures filtered by group=4 (tasks by user 3 in group 4)
    result_group_4 = workflow_analytics.get_task_failures(group=4)

    # THEN we should get the correct failures for group 4's tasks
    expected_result_group_4 = {
        ("task1", "v1"): ["ErrorC"],
        ("task2", "v1"): ["ErrorD"]
    }
    assert result_group_4 == expected_result_group_4

    # WHEN calling get_task_failures filtered by both user=3 and group=4
    result_user_3_group_4 = workflow_analytics.get_task_failures(user=3, group=4)

    # THEN we should get the correct failures for user 3 in group 4
    expected_result_user_3_group_4 = {
        ("task1", "v1"): ["ErrorC"],
        ("task2", "v1"): ["ErrorD"]
    }
    assert result_user_3_group_4 == expected_result_user_3_group_4


def test_wfanalytics_get_task_usage_over_time_with_filters(workflow_tracker_and_workflow_analytics):
    # GIVEN a WorkflowTracker event system and workflow analytics listener
    workflow_tracker: WorkflowTracker
    workflow_analytics: WorkflowAnalytics
    workflow_tracker, workflow_analytics = workflow_tracker_and_workflow_analytics

    # GIVEN task data added to workflow_analytics.tasks with specific execution times for different users and groups
    workflow_id_1 = workflow_tracker.initiate_workflow(
        "Test Workflow 1", "Test Description", user=1, group=2)
    workflow_id_2 = workflow_tracker.initiate_workflow(
        "Test Workflow 2", "Test Description", user=3, group=4)

    # Simulating task executions for task1 on different dates for user 1, group 2
    task_id_1 = workflow_tracker.add_task_to_workflow(
        workflow_id_1, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_1]["start_time"] = datetime(2023, 8, 1, 12, 0, 0)

    task_id_2 = workflow_tracker.add_task_to_workflow(
        workflow_id_1, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_2]["start_time"] = datetime(2023, 8, 2, 13, 0, 0)

    # Simulating task executions for task1 on different dates for user 3, group 4
    task_id_3 = workflow_tracker.add_task_to_workflow(
        workflow_id_2, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_3]["start_time"] = datetime(2023, 8, 2, 14, 0, 0)

    task_id_4 = workflow_tracker.add_task_to_workflow(
        workflow_id_2, "task1", "v1", {"foo": "bar"}, {"bar": "baz"})
    workflow_analytics.tasks[task_id_4]["start_time"] = datetime(2023, 8, 3, 15, 0, 0)

    # Manually update the view table with the tasks' details
    workflow_analytics.update_view_table(task_id_1)
    workflow_analytics.update_view_table(task_id_2)
    workflow_analytics.update_view_table(task_id_3)
    workflow_analytics.update_view_table(task_id_4)

    # WHEN calling get_task_usage_over_time without filters for task1
    result_all = workflow_analytics.get_task_usage_over_time(task_name="task1")

    # THEN we should get the correct usage counts over time for all users and groups
    expected_result_all = {
        str(datetime(2023, 8, 1).date()): 1,
        str(datetime(2023, 8, 2).date()): 2,
        str(datetime(2023, 8, 3).date()): 1
    }
    assert result_all == expected_result_all

    # WHEN calling get_task_usage_over_time filtered by user=1 (group=2 implicitly)
    result_user_1 = workflow_analytics.get_task_usage_over_time(task_name="task1", user=1)

    # THEN we should get the correct usage counts over time for user 1
    expected_result_user_1 = {
        str(datetime(2023, 8, 1).date()): 1,
        str(datetime(2023, 8, 2).date()): 1
    }
    assert result_user_1 == expected_result_user_1

    # WHEN calling get_task_usage_over_time filtered by group=4 (tasks by user 3 in group 4)
    result_group_4 = workflow_analytics.get_task_usage_over_time(task_name="task1", group=4)

    # THEN we should get the correct usage counts over time for group 4
    expected_result_group_4 = {
        str(datetime(2023, 8, 2).date()): 1,
        str(datetime(2023, 8, 3).date()): 1
    }
    assert result_group_4 == expected_result_group_4

    # WHEN calling get_task_usage_over_time filtered by both user=3 and group=4
    result_user_3_group_4 = workflow_analytics.get_task_usage_over_time(task_name="task1", user=3, group=4)

    # THEN we should get the correct usage counts over time for user 3 in group 4
    expected_result_user_3_group_4 = {
        str(datetime(2023, 8, 2).date()): 1,
        str(datetime(2023, 8, 3).date()): 1
    }
    assert result_user_3_group_4 == expected_result_user_3_group_4

