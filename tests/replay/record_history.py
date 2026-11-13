# -*- coding: utf-8 -*-
# Copyright 2024 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Record the event history that one released BIOMERO version writes.

Every install keeps the events written by the BIOMERO release it ran, and a
newer release must replay them (see "Event history replay" in
``docs/developer/eventsourcing.rst``). This script produces the fixture that
``tests/unit/test_replay_histories.py`` replays: it installs the requested
release into a throwaway virtual environment, runs a representative workflow
scenario with that release's own ``WorkflowTracker`` and SQLite persistence
(no Slurm, no OMERO, nothing mocked in the event path), and dumps the stored
events plus the state that release itself sees after replaying them.

Usage, from the repository root and with any Python able to create virtual
environments::

    python tests/replay/record_history.py 2.9.0b25
    python tests/replay/record_history.py 2.8.3 --venv-dir <scratch>/venv-replay-2.8.3

The fixture is written to ``tests/replay/histories/<version>.json``. Commit it
unchanged. Do not edit or re-record a fixture of an older release: it is the
proof of what that release wrote.

Fixture format (``format`` 1):

* ``biomero_version`` and ``recorded_with``: the release and the library
  versions of the throwaway environment.
* ``scenario``: labels for the recorded workflows, tasks and maintenance
  requests (name to UUID) and the ``features`` this release supports.
* ``events``: the notification log, in order, each with ``notification_id``,
  ``originator_id``, ``originator_version``, ``topic`` and the decoded JSON
  ``state``.
* ``expected.aggregates``: per aggregate UUID, its ``class``, ``version`` and
  ``state`` as the recording release itself replays it.
* ``expected.views``: the rows of the four BIOMERO view tables as that
  release projected them.

The script runs in two modes. Without ``--inside-venv`` it creates the
environment, installs the release and starts itself again with that
environment's interpreter; with ``--inside-venv`` it records the scenario and
therefore imports only the installed release, never this repository.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

HISTORY_FORMAT = 1
HISTORIES_DIR = Path(__file__).resolve().parent / "histories"
PACKAGE_NAME = "biomero"
INSTALL_TIMEOUT_SECONDS = 900
RECORD_TIMEOUT_SECONDS = 600
VENV_NAME_TEMPLATE = "venv-replay-{version}"
DATABASE_NAME = "recording.sqlite"

# The user and group that own the recorded workflows. Fixed, so that a fixture
# is recognisable; the values are meaningless outside the fixture.
USER_ID = 1
GROUP_ID = 3

# Statuses a Slurm job passes through, as SlurmJob reports them to the tracker.
JOB_STATUS_PENDING = "PENDING"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_COMPLETED = "COMPLETED"
JOB_STATUS_FAILED = "FAILED"

# Task names of the BIOMERO pipeline; the views derive workflow status from them.
TASK_TRANSFER = "_SLURM_Image_Transfer.py"
TASK_CONVERT = "convert_zarr_to_tiff"
TASK_RESULTS = "SLURM_Get_Results.py"
TASK_LAUNCHER = "SLURM_Run_Workflow.py"
TASK_BATCHED = "SLURM_Run_Workflow_Batched.py"
TASK_REMOTE_CONVERSION = "SLURM_Remote_Conversion.py"
TASK_IMPORT = "SLURM_Import_Results.py"
TASK_FILE_TRANSFER = "_SLURM_File_Transfer.py"
# Statuses of the import task; the workflow view maps them to phases.
IMPORT_STATUSES = ("IMPORTING", "IMPORTED", "POSTPROCESSING")
# User and group of a workflow with an unknown owner, as 2.0.0 wrote.
UNKNOWN_OWNER = -1

# Labels of the recorded workflows; the replay tests look them up by
# name. A task label is "<workflow label>.<role>".
WF_COMPLETED = "completed"
WF_FAILED = "failed"
WF_RUNNING = "running"
WF_BATCHED = "batched"
WF_LEGACY_OWNER = "legacy_owner"
WF_CLAIMED = "claimed"
WF_CANCEL_REQUESTED = "cancel_requested"
LAUNCHER_SCRIPT_VERSION = "2.9.0"
LAUNCHER_OWNER = "replay-recorder"

# Library bookkeeping attributes of an aggregate, under their public names.
AGGREGATE_ATTRIBUTE_NAMES = {
    "_id": "id", "_version": "version", "_created_on": "created_on",
    "_modified_on": "modified_on"}

# View tables in biomero.database; present in every release with event sourcing.
VIEW_MODEL_NAMES = ("JobView", "JobProgressView", "WorkflowProgressView",
                    "TaskExecution")


# ------------------------------ outer mode ------------------------------ #

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv (list, optional): Arguments, without the program name.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Record the event history written by a released "
                    "BIOMERO version into tests/replay/histories/.")
    parser.add_argument("version",
                        help="Release to record, for example 2.8.3 or "
                             "2.9.0b25. Must exist on PyPI.")
    parser.add_argument("--venv-dir", type=Path,
                        help="Throwaway virtual environment to create. "
                             "Must not exist yet. Defaults to "
                             f"<tempdir>/{VENV_NAME_TEMPLATE}.")
    parser.add_argument("--keep-venv", action="store_true",
                        help="Do not delete the virtual environment "
                             "afterwards.")
    parser.add_argument("--output-dir", type=Path, default=HISTORIES_DIR,
                        help="Where to write <version>.json. "
                             "Defaults to tests/replay/histories.")
    parser.add_argument("--python", default=sys.executable,
                        help="Interpreter that creates the environment. "
                             "It must satisfy the release's Requires-Python. "
                             "Defaults to the running interpreter.")
    parser.add_argument("--inside-venv", action="store_true",
                        help="Internal: record inside the prepared "
                             "environment.")
    parser.add_argument("--workdir", type=Path,
                        help="Internal: scratch folder of the recording.")
    return parser.parse_args(argv)


def venv_python(venv_dir: Path) -> Path:
    """Path of the interpreter inside a virtual environment.

    Args:
        venv_dir (Path): The virtual environment folder.

    Returns:
        Path: The interpreter on this platform.
    """
    windows_python = venv_dir / "Scripts" / "python.exe"
    return windows_python if windows_python.exists() \
        else venv_dir / "bin" / "python"


def record_in_throwaway_venv(args: argparse.Namespace) -> int:
    """Install the release in a new environment and record it there.

    Args:
        args (argparse.Namespace): The parsed arguments.

    Returns:
        int: The process exit code.
    """
    venv_dir = args.venv_dir or Path(tempfile.gettempdir()) / \
        VENV_NAME_TEMPLATE.format(version=args.version)
    if venv_dir.exists():
        # Never reuse or delete a folder this run did not create.
        print(f"{venv_dir} already exists; remove it or pass --venv-dir",
              file=sys.stderr)
        return 1
    workdir = Path(tempfile.mkdtemp(prefix="biomero-record-"))
    try:
        print(f"Creating {venv_dir}")
        subprocess.run([args.python, "-m", "venv", str(venv_dir)],
                       check=True, timeout=INSTALL_TIMEOUT_SECONDS)
        recording_python = str(venv_python(venv_dir))
        print(f"Installing {PACKAGE_NAME}=={args.version}")
        subprocess.run([recording_python, "-m", "pip", "install", "--quiet",
                        f"{PACKAGE_NAME}=={args.version}"],
                       check=True, timeout=INSTALL_TIMEOUT_SECONDS)
        # -I: the recording imports the installed release, never this
        # repository (no script folder, working directory or PYTHONPATH).
        subprocess.run([recording_python, "-I", str(Path(__file__).resolve()),
                        args.version, "--inside-venv",
                        "--workdir", str(workdir),
                        "--output-dir", str(args.output_dir)],
                       check=True, timeout=RECORD_TIMEOUT_SECONDS,
                       env={**os.environ, "PYTHONWARNINGS": "ignore"})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if not args.keep_venv:
            shutil.rmtree(venv_dir, ignore_errors=True)
    return 0


# ------------------------------ inner mode ------------------------------ #

def to_json_value(value: Any) -> Any:
    """Convert state to JSON types, readable and independent of the library.

    Args:
        value (Any): A value from an aggregate or a database row.

    Returns:
        Any: The same value with UUIDs and datetimes as strings.

    Raises:
        TypeError: If the value has no defined JSON form; record it
            explicitly instead of guessing.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    raise TypeError(f"No JSON form for {type(value).__name__}: {value!r}")


def create_client(database_url: str) -> Any:
    """Create a SlurmClient that tracks workflows, without any connection.

    ``config_only`` skips the SSH validation, so the client's own
    ``initialize_analytics_system`` builds the tracker and the four view
    listeners exactly as in production.

    Args:
        database_url (str): SQLAlchemy URL of the recording database.

    Returns:
        SlurmClient: The client, with ``workflowTracker`` and the listeners.
    """
    from biomero import SlurmClient

    client = SlurmClient(config_only=True, host="localhost")
    client.track_workflows = True
    client.enable_job_accounting = True
    client.enable_job_progress = True
    client.enable_workflow_analytics = True
    client.sqlalchemy_url = database_url
    client.initialize_analytics_system(reset_tables=False)
    return client


def commit_pending_events() -> None:
    """Commit what aggregates saved directly through ``tracker.save``."""
    from biomero.database import EngineManager

    commit = getattr(EngineManager, "safe_commit", EngineManager.commit)
    commit()


def sbatch_result(job_id: int) -> Any:
    """A Fabric result like the one of an ``sbatch`` call.

    Args:
        job_id (int): The Slurm job ID the result reports.

    Returns:
        fabric.Result: The result as the tracker stores it.
    """
    from fabric import Result

    return Result(connection=None, command="sbatch job.sh", exited=0,
                  stdout=f"Submitted batch job {job_id}\n", stderr="")


def record_job(tracker: Any, task_id: UUID, job_id: int,
               statuses: List[str], progresses: List[str]) -> None:
    """Record a Slurm job on a task the way the SlurmJob monitor does.

    Args:
        tracker (WorkflowTracker): The release's tracker.
        task_id (UUID): The task that owns the job.
        job_id (int): The Slurm job ID.
        statuses (list): The statuses the job passed through, in order.
        progresses (list): The progress strings reported, in order.
    """
    tracker.start_task(task_id)
    tracker.add_job_id(task_id, job_id)
    tracker.add_result(task_id, sbatch_result(job_id))
    for status in statuses:
        tracker.update_task_status(task_id, status)
    for progress in progresses:
        tracker.update_task_progress(task_id, progress)


def task_label(workflow_label: str, role: str) -> str:
    """Label of a task in the scenario registry.

    Args:
        workflow_label (str): One of the ``WF_*`` labels.
        role (str): What the task does in that workflow.

    Returns:
        str: ``"<workflow label>.<role>"``.
    """
    return f"{workflow_label}.{role}"


def register_workflow(scenario: Dict, label: str, workflow_id: UUID) -> None:
    """Add a workflow to the scenario label registry.

    Args:
        scenario (dict): The registry.
        label (str): One of the ``WF_*`` labels.
        workflow_id (UUID): The recorded workflow.
    """
    scenario["workflows"][label] = workflow_id


def register_tasks(scenario: Dict, workflow_label: str,
                   tasks_by_role: Dict[str, UUID]) -> None:
    """Add the tasks of a workflow to the scenario label registry.

    Args:
        scenario (dict): The registry.
        workflow_label (str): One of the ``WF_*`` labels.
        tasks_by_role (dict): Role to recorded task ID.
    """
    for role, task_id in tasks_by_role.items():
        scenario["tasks"][task_label(workflow_label, role)] = task_id


def record_completed_workflow(tracker: Any, scenario: Dict) -> None:
    """Record a workflow that ran to completion through every pipeline phase.

    Args:
        tracker (WorkflowTracker): The tracker of the release.
        scenario (dict): Label registry; receives the IDs recorded here.
    """
    workflow_id = tracker.initiate_workflow(
        "cellpose", "Completed run recorded for replay tests",
        USER_ID, GROUP_ID)
    register_workflow(scenario, WF_COMPLETED, workflow_id)
    tracker.start_workflow(workflow_id)

    transfer_id = tracker.add_task_to_workflow(
        workflow_id, TASK_TRANSFER, "1.0", {"Image": [101, 102]},
        {"Folder": "/data/input"})
    tracker.start_task(transfer_id)
    tracker.complete_task(transfer_id, "Exported 2 images")

    convert_id = tracker.add_task_to_workflow(
        workflow_id, TASK_CONVERT, "v0.9.1", {"Image": [101, 102]},
        {"to": "tiff"})
    record_job(tracker, convert_id, 1001,
               [JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED],
               ["50%", "100%"])
    tracker.complete_task(convert_id, "Converted")

    remote_conversion_id = tracker.add_task_to_workflow(
        workflow_id, TASK_REMOTE_CONVERSION, "1.0", {"Image": [101, 102]},
        {"to": "zarr"})
    record_job(tracker, remote_conversion_id, 1004,
               [JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED],
               ["100%"])
    tracker.complete_task(remote_conversion_id, "Converted remotely")

    run_id = tracker.add_task_to_workflow(
        workflow_id, "cellpose", "v1.2.1", {"Image": [101, 102]},
        {"diameter": 30, "model": "cyto"})
    record_job(tracker, run_id, 1002,
               [JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_COMPLETED],
               ["10%", "60%", "100%"])
    tracker.add_job_id(run_id, 1003)  # a second job on the same task
    tracker.complete_task(run_id, "Job 1002 completed")

    results_id = tracker.add_task_to_workflow(
        workflow_id, TASK_RESULTS, "1.0", {"Image": [101, 102]},
        {"slurm_job_id": 1002})
    tracker.start_task(results_id)
    tracker.complete_task(results_id, "Retrieved 2 results")

    import_id = tracker.add_task_to_workflow(
        workflow_id, TASK_IMPORT, "1.0", {"Image": [101, 102]},
        {"slurm_job_id": 1002})
    tracker.start_task(import_id)
    for status in IMPORT_STATUSES:
        tracker.update_task_status(import_id, status)
    tracker.complete_task(import_id, "Imported 2 results")
    tracker.complete_workflow(workflow_id)

    register_tasks(scenario, WF_COMPLETED, {
        "transfer": transfer_id, "convert": convert_id,
        "remote_conversion": remote_conversion_id, "run": run_id,
        "results": results_id, "import": import_id})


def record_failed_workflow(tracker: Any, scenario: Dict) -> None:
    """Record a workflow whose Slurm job failed.

    Args:
        tracker (WorkflowTracker): The tracker of the release.
        scenario (dict): Label registry; receives the IDs recorded here.
    """
    workflow_id = tracker.initiate_workflow(
        "cellexpansion", "Failed run recorded for replay tests",
        USER_ID + 1, GROUP_ID)
    register_workflow(scenario, WF_FAILED, workflow_id)
    tracker.start_workflow(workflow_id)
    transfer_id = tracker.add_task_to_workflow(
        workflow_id, TASK_TRANSFER, "1.0", {"Image": [201]}, {})
    tracker.start_task(transfer_id)
    tracker.complete_task(transfer_id, "Exported 1 image")

    run_id = tracker.add_task_to_workflow(
        workflow_id, "cellexpansion", "v1.0.0", {"Image": [201]},
        {"expansion": 5})
    record_job(tracker, run_id, 1100,
               [JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_FAILED],
               ["20%"])
    error_message = "Slurm job 1100 ended in state FAILED"
    tracker.fail_task(run_id, error_message)
    tracker.fail_workflow(workflow_id, error_message)
    register_tasks(scenario, WF_FAILED,
                   {"transfer": transfer_id, "run": run_id})


def record_running_workflow(tracker: Any, scenario: Dict) -> None:
    """Record a workflow that is still running, as after a restart.

    The job reports its progress before its status, which makes the workflow
    view interpolate the workflow progress between 50% and 90%.

    Args:
        tracker (WorkflowTracker): The tracker of the release.
        scenario (dict): Label registry; receives the IDs recorded here.
    """
    workflow_id = tracker.initiate_workflow(
        "stardist", "Running run recorded for replay tests",
        USER_ID, GROUP_ID + 1)
    register_workflow(scenario, WF_RUNNING, workflow_id)
    tracker.start_workflow(workflow_id)
    transfer_id = tracker.add_task_to_workflow(
        workflow_id, TASK_TRANSFER, "1.0", {"Image": [301]}, {})
    tracker.start_task(transfer_id)
    tracker.complete_task(transfer_id, "Exported 1 image")

    run_id = tracker.add_task_to_workflow(
        workflow_id, "stardist", "v2.0.0", {"Image": [301]}, {"prob": 0.5})
    record_job(tracker, run_id, 1200, [JOB_STATUS_PENDING], [])
    tracker.update_task_progress(run_id, "43%")
    tracker.update_task_status(run_id, JOB_STATUS_RUNNING)
    results_id = tracker.add_task_to_workflow(  # created, never started
        workflow_id, TASK_RESULTS, "1.0", {"Image": [301]}, {})
    register_tasks(scenario, WF_RUNNING, {
        "transfer": transfer_id, "run": run_id, "results": results_id})


def record_batched_workflow(tracker: Any, scenario: Dict) -> None:
    """Record a running batched workflow that transfers files.

    Args:
        tracker (WorkflowTracker): The tracker of the release.
        scenario (dict): Label registry; receives the IDs recorded here.
    """
    workflow_id = tracker.initiate_workflow(
        "batched", "Batched run recorded for replay tests",
        USER_ID, GROUP_ID)
    register_workflow(scenario, WF_BATCHED, workflow_id)
    tracker.start_workflow(workflow_id)
    file_transfer_id = tracker.add_task_to_workflow(
        workflow_id, TASK_FILE_TRANSFER, "1.0", {"File": [11]}, {})
    tracker.start_task(file_transfer_id)
    tracker.complete_task(file_transfer_id, "Transferred 1 file")
    batched_id = tracker.add_task_to_workflow(
        workflow_id, TASK_BATCHED, "1.0", {"Image": [401, 402]},
        {"batch": 1})
    tracker.start_task(batched_id)
    register_tasks(scenario, WF_BATCHED, {
        "file_transfer": file_transfer_id, "batched": batched_id})


def record_legacy_owner_workflow(tracker: Any, scenario: Dict) -> None:
    """Record a workflow with the user and group -1 that 2.0.0 wrote.

    Args:
        tracker (WorkflowTracker): The tracker of the release.
        scenario (dict): Label registry; receives the IDs recorded here.
    """
    workflow_id = tracker.initiate_workflow(
        "legacy", "Workflow without owner recorded for replay tests",
        UNKNOWN_OWNER, UNKNOWN_OWNER)
    register_workflow(scenario, WF_LEGACY_OWNER, workflow_id)
    tracker.start_workflow(workflow_id)
    run_id = tracker.add_task_to_workflow(
        workflow_id, "legacy", "v0.1.0", {"Image": [501]}, {})
    tracker.start_task(run_id)
    tracker.complete_task(run_id, "Done")
    tracker.complete_workflow(workflow_id)
    register_tasks(scenario, WF_LEGACY_OWNER, {"run": run_id})


def record_storage_provenance(tracker: Any, scenario: Dict) -> bool:
    """Record storage provenance on the completed results task.

    Args:
        tracker (WorkflowTracker): The release's tracker.
        scenario (dict): Label registry holding the completed tasks.

    Returns:
        bool: Whether the release supports the event.
    """
    if not hasattr(tracker, "record_storage_provenance"):
        return False
    tracker.record_storage_provenance(
        scenario["tasks"][task_label(WF_COMPLETED, "import")], "Plate:10",
        {"storage": "shallow-zarr", "location": "importer"})
    return True


def record_canonical_inputs(tracker: Any, scenario: Dict) -> bool:
    """Record the canonical Zarr inputs of the completed workflow.

    Args:
        tracker (WorkflowTracker): The release's tracker.
        scenario (dict): Label registry holding the completed workflow.

    Returns:
        bool: Whether the release supports the event.
    """
    if not hasattr(tracker, "record_canonical_inputs"):
        return False
    try:  # the contracts live in biomero-schema; early 2.9 betas re-exported
        from biomero_schema.zarr import (
            CanonicalInput, CanonicalZarrSource, PixelIdentity)
    except ImportError:
        from biomero.zarr_contracts import (
            CanonicalInput, CanonicalZarrSource, PixelIdentity)

    identity = PixelIdentity(
        node_path=".", role="image", iscc_code="ISCC:KPIXEL",
        data_code="ISCC:GDATA", instance_code="ISCC:IINSTANCE",
        tool_version="0.1.0", imagewalk_revision="draft-2026-06",
        shape=(1, 1, 1, 16, 16), dtype="uint16",
        axes=("t", "c", "z", "y", "x"))
    source = CanonicalZarrSource(
        storage_root="group-3-data",
        relative_path=".processed/Image-101.g1.ome.zarr", node_path=".",
        source_object_type="Image", source_object_id=101,
        source_generation=1, interchange_profile="ngff-0.4-zarr-v2",
        pixel_identity=identity, pixel_identity_origin="raw",
        canonical_pixel_verified=True)
    canonical_input = CanonicalInput(
        ordinal=0, selected_object_type="Image", selected_object_id=101,
        source=source)
    tracker.record_canonical_inputs(
        scenario["workflows"][WF_COMPLETED],
        scenario["tasks"][task_label(WF_COMPLETED, "transfer")],
        [canonical_input])
    return True


def record_claimed_launcher(tracker: Any, scenario: Dict) -> bool:
    """Record a detached launcher task that a worker has claimed.

    Releases differ in how a launcher is marked and claimed (2.9 betas keep
    both in ``biomero.detached`` and claim by task status; later releases
    have tracker methods and a claim event), so each step uses what the
    release offers.

    Args:
        tracker (WorkflowTracker): The release's tracker.
        scenario (dict): Label registry; receives the IDs recorded here.

    Returns:
        bool: Whether the release supports detached launchers.
    """
    try:
        import biomero.constants as constants
        import biomero.detached as detached
    except ImportError:
        return False
    marker = getattr(constants, "LAUNCHER_MARKER", None) \
        or detached.LAUNCHER_MARKER
    launcher_params = {marker: LAUNCHER_SCRIPT_VERSION,
                       "workflows": ["cellpose"]}
    workflow_id = tracker.initiate_workflow(
        "detached", "Claimed launcher recorded for replay tests",
        USER_ID, GROUP_ID)
    tracker.start_workflow(workflow_id)
    add_launcher = getattr(tracker, "add_launcher_task",
                           tracker.add_task_to_workflow)
    launcher_id = add_launcher(
        workflow_id, TASK_LAUNCHER, LAUNCHER_SCRIPT_VERSION, [101],
        launcher_params)
    if hasattr(tracker, "claim_launcher_task"):
        tracker.claim_launcher_task(launcher_id, LAUNCHER_OWNER)
    else:
        detached.claim_launcher_task(tracker, launcher_id)
    register_workflow(scenario, WF_CLAIMED, workflow_id)
    register_tasks(scenario, WF_CLAIMED, {"launcher": launcher_id})
    return True


def record_cancel_request(tracker: Any, scenario: Dict) -> bool:
    """Record a running workflow for which a cancellation was requested.

    Args:
        tracker (WorkflowTracker): The release's tracker.
        scenario (dict): Label registry; receives the IDs recorded here.

    Returns:
        bool: Whether the release supports cancellation requests.
    """
    if not hasattr(tracker, "request_workflow_cancel"):
        return False
    workflow_id = tracker.initiate_workflow(
        "cancelling", "Cancel request recorded for replay tests",
        USER_ID, GROUP_ID)
    tracker.start_workflow(workflow_id)
    tracker.request_workflow_cancel(workflow_id, "Cancelled by the user")
    register_workflow(scenario, WF_CANCEL_REQUESTED, workflow_id)
    return True


def record_metadata_refreshes(tracker: Any, scenario: Dict) -> bool:
    """Record a finished and a running metadata maintenance request.

    Args:
        tracker (WorkflowTracker): The release's tracker.
        scenario (dict): Label registry; receives the IDs recorded here.

    Returns:
        bool: Whether the release supports maintenance requests.
    """
    try:
        from biomero.maintenance import queue_metadata_refresh
    except ImportError:
        return False
    finished_id = queue_metadata_refresh(
        tracker, USER_ID, GROUP_ID, {"workers": 4})
    finished = tracker.repository.get(finished_id)
    finished.started()
    finished.progressed({"processed": 25, "counts": {"updated": 25}})
    finished.finished({"counts": {"failed": 0}})
    tracker.save(finished)
    commit_pending_events()

    running_id = queue_metadata_refresh(
        tracker, USER_ID, GROUP_ID, {"workers": 2})
    running = tracker.repository.get(running_id)
    running.started()
    running.progressed({"processed": 5, "counts": {"updated": 5}})
    tracker.save(running)
    commit_pending_events()
    scenario["metadata_refreshes"].update(
        {"finished": finished_id, "running": running_id})
    return True


def record_scenario(tracker: Any) -> Dict:
    """Run the whole scenario on the release's tracker.

    Args:
        tracker (WorkflowTracker): The release's tracker.

    Returns:
        dict: ``workflows``, ``tasks``, ``metadata_refreshes`` (label to
            UUID) and the ``features`` the release supports beyond the base
            event set.
    """
    scenario: Dict[str, Any] = {
        "workflows": {}, "tasks": {}, "metadata_refreshes": {},
        "features": []}
    record_completed_workflow(tracker, scenario)
    record_failed_workflow(tracker, scenario)
    record_running_workflow(tracker, scenario)
    record_batched_workflow(tracker, scenario)
    record_legacy_owner_workflow(tracker, scenario)
    optional_features = {
        "storage_provenance": record_storage_provenance,
        "canonical_inputs": record_canonical_inputs,
        "claimed_launcher": record_claimed_launcher,
        "cancel_request": record_cancel_request,
        "metadata_refresh": record_metadata_refreshes}
    for feature, record in optional_features.items():
        if record(tracker, scenario):
            scenario["features"].append(feature)
    return scenario


def dump_events(tracker: Any) -> List[Dict]:
    """Read the notification log as readable records.

    Args:
        tracker (WorkflowTracker): A tracker on the recorded store.

    Returns:
        list: One dict per stored event, in log order.

    Raises:
        ValueError: If an event state is not plain JSON (compressed or
            encrypted stores need their own reader).
    """
    events: List[Dict] = []
    start = 1
    section_size = tracker.notification_log.section_size
    while True:
        notifications = tracker.notification_log.select(
            start=start, limit=section_size)
        for notification in notifications:
            if not notification.state.startswith(b"{"):
                raise ValueError(
                    f"State of notification {notification.id} is not JSON")
            events.append({
                "notification_id": notification.id,
                "originator_id": str(notification.originator_id),
                "originator_version": notification.originator_version,
                "topic": notification.topic,
                "state": json.loads(notification.state)})
            start = notification.id + 1
        if len(notifications) < section_size:
            return events


def dump_aggregates(tracker: Any, events: List[Dict]) -> Dict[str, Dict]:
    """Replay every aggregate and capture the state the release sees.

    Args:
        tracker (WorkflowTracker): A tracker on the recorded store.
        events (list): The dumped events, naming the aggregates.

    Returns:
        dict: Aggregate UUID to ``class``, ``version`` and ``state``.
    """
    aggregates: Dict[str, Dict] = {}
    for originator_id in dict.fromkeys(e["originator_id"] for e in events):
        aggregate = tracker.repository.get(UUID(originator_id))
        state = {}
        for name, value in vars(aggregate).items():
            if name == "_pending_events":
                continue
            state[AGGREGATE_ATTRIBUTE_NAMES.get(name, name)] = \
                to_json_value(value)
        aggregates[originator_id] = {
            "class": type(aggregate).__name__,
            "version": aggregate.version,
            "state": state}
    return aggregates


def dump_views() -> Dict[str, List[Dict]]:
    """Read the four view tables as the release projected them.

    Returns:
        dict: Table name to its rows, ordered by primary key.
    """
    from sqlalchemy import inspect
    from biomero import database
    from biomero.database import EngineManager

    views: Dict[str, List[Dict]] = {}
    with EngineManager.get_session() as session:
        for model_name in VIEW_MODEL_NAMES:
            model = getattr(database, model_name)
            columns = inspect(model).columns
            primary_key = [column.name for column in inspect(model).primary_key]
            rows = [{column.name: to_json_value(getattr(row, column.key))
                     for column in columns}
                    for row in session.query(model).all()]
            rows.sort(key=lambda row: [str(row[key]) for key in primary_key])
            views[model.__tablename__] = rows
    return views


def check_views_complete(views: Dict[str, List[Dict]],
                         aggregates: Dict[str, Dict]) -> None:
    """Fail if a projection missed an aggregate while recording.

    SQLite can briefly lock a view update, which the release logs and skips;
    a fixture with such a gap would promise rows nobody can reproduce.

    Args:
        views (dict): The dumped view rows by table.
        aggregates (dict): The dumped aggregates.

    Raises:
        RuntimeError: If a workflow or task has no view row; record again.
    """
    counts = {name: sum(1 for aggregate in aggregates.values()
                        if aggregate["class"] == name)
              for name in ("WorkflowRun", "Task")}
    rows = {"WorkflowRun": len(views["biomero_workflow_progress_view"]),
            "Task": len(views["biomero_task_execution"])}
    if counts != rows:
        raise RuntimeError(
            f"Views are incomplete (aggregates {counts}, rows {rows}); "
            "run the recording again")


def library_versions() -> Dict[str, str]:
    """Versions of the libraries the recording ran on.

    Returns:
        dict: Distribution name to installed version.
    """
    from importlib.metadata import version

    names = ("eventsourcing", "eventsourcing_sqlalchemy", "sqlalchemy")
    return {"python": sys.version.split()[0],
            **{name: version(name) for name in names}}


def record_inside_venv(args: argparse.Namespace) -> int:
    """Record the scenario with the release installed in this environment.

    Args:
        args (argparse.Namespace): The parsed arguments.

    Returns:
        int: The process exit code.

    Raises:
        RuntimeError: If another version than requested is installed.
    """
    from importlib.metadata import version

    installed_version = version(PACKAGE_NAME)
    if installed_version != args.version:
        raise RuntimeError(
            f"Expected {PACKAGE_NAME} {args.version}, found {installed_version}")
    database_url = f"sqlite:///{(args.workdir / DATABASE_NAME).as_posix()}"
    os.environ["PERSISTENCE_MODULE"] = "eventsourcing_sqlalchemy"
    os.environ["SQLALCHEMY_URL"] = database_url

    from biomero.database import EngineManager
    from biomero.eventsourcing import WorkflowTracker

    client = create_client(database_url)
    scenario = record_scenario(client.workflowTracker)
    views = dump_views()
    for name in ("workflowTracker", "jobAccounting", "jobProgress",
                 "wfProgress", "workflowAnalytics"):
        getattr(client, name).close()
    EngineManager.close_engine()

    # Replay from the store with a new tracker, as a restarted process does.
    replaying_tracker = WorkflowTracker()
    events = dump_events(replaying_tracker)
    aggregates = dump_aggregates(replaying_tracker, events)
    replaying_tracker.close()
    check_views_complete(views, aggregates)

    history = {
        "format": HISTORY_FORMAT,
        "biomero_version": installed_version,
        "recorded_on": date.today().isoformat(),
        "recorded_with": library_versions(),
        "scenario": to_json_value(scenario),
        "events": events,
        "expected": {"aggregates": aggregates, "views": views}}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"{installed_version}.json"
    # newline="\n": Windows must not turn the fixture into CRLF.
    with output_path.open("w", encoding="utf-8", newline="\n") as fixture:
        fixture.write(json.dumps(history, indent=2) + "\n")
    print(f"Wrote {len(events)} events of {len(aggregates)} aggregates "
          f"to {output_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Run in the mode the arguments ask for.

    Args:
        argv (list, optional): Arguments, without the program name.

    Returns:
        int: The process exit code.
    """
    args = parse_args(argv)
    if args.inside_venv:
        return record_inside_venv(args)
    return record_in_throwaway_venv(args)


if __name__ == "__main__":
    sys.exit(main())
