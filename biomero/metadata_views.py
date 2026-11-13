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
"""Versioned OMERO key/value views of immutable workflow history.

The event store is the authoritative record of a workflow. This module renders
(and plans refreshes of) the searchable key/value *views* of that history that
the scripts attach to OMERO objects. It neither writes events nor changes OMERO
objects: it only returns plain data structures that the caller applies.

Full CSV export is deliberately independent of this searchable, selectively
reduced view. The textual contract with the scripts that write the views is
pinned in ``tests/unit/test_metadata_views.py``.
"""
import logging
import re
from dataclasses import dataclass
from typing import (
    Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Set,
    Tuple,
)
from uuid import UUID

from biomero.constants import (
    IMAGE_IMPORT_SCRIPT,
    IMPORT_RESULTS_SCRIPT,
    LAUNCHER_MARKER,
    REMOTE_SHALLOWER_TASK_NAME,
    RUN_WF_BATCHED_SCRIPT,
    RUN_WF_SCRIPT,
)
from biomero.eventsourcing import is_launcher_task

logger = logging.getLogger(__name__)

NAMESPACE = "biomero/workflow"
TASK_NAMESPACE_PREFIX = f"{NAMESPACE}/task/"
JOB_NAMESPACE_SUFFIX = "/job"

# The only metadata view layout. It is written to ``Metadata_View_Version`` so
# a later layout can be told apart from existing annotations.
METADATA_VIEW_VERSION = "v0"
SUPPORTED_METADATA_VIEW_VERSIONS = (METADATA_VIEW_VERSION,)

# Result tasks whose annotations the scripts discover under the Get Results
# namespace, whichever of the two scripts ran the import.
RESULT_TASK_NAMES = (IMPORT_RESULTS_SCRIPT, IMAGE_IMPORT_SCRIPT)
# Orchestration tasks that can carry parameters of several workflows.
ORCHESTRATION_TASK_NAMES = (RUN_WF_SCRIPT, RUN_WF_BATCHED_SCRIPT)

# Task parameters are prefixed in the view, so they cannot clash with the
# fixed task fields. Job environment variables get their own prefix.
PARAM_PREFIX = "Param_"
ENV_PREFIX = "Env_"
OUTPUT_SETTINGS_PARAM = "output_settings"
# Parameters of an orchestration task that are qualified by workflow name:
# "<workflow> | <parameter>" and the older "<workflow>_|_<parameter>".
WORKFLOW_PARAM_SEPARATORS = (" | ", "_|_")
WORKFLOW_VERSION_SUFFIX = "_Version"
# Orchestration parameters that only duplicate the workflow descriptors
# (wf_params_, wf_file_params_) or are BIOMERO bookkeeping such as the
# launcher marker (_biomero_).
WF_PARAMS_PREFIX = "wf_params_"
WF_FILE_PARAMS_PREFIX = "wf_file_params_"
# The launcher marker and the other BIOMERO bookkeeping parameters share the
# leading "_biomero_".
BIOMERO_PARAM_PREFIX = LAUNCHER_MARKER[:LAUNCHER_MARKER.index("_", 1) + 1]
EXCLUDED_PARAM_PREFIXES = (
    WF_PARAMS_PREFIX, WF_FILE_PARAMS_PREFIX, BIOMERO_PARAM_PREFIX)

UNKNOWN_SOFTWARE_VERSION = "Unknown"
SOFTWARE_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
STORAGE_SHALLOW_ZARR = "shallow-zarr"
STORAGE_FULL_ZARR = "full-zarr"
STORAGE_FORMATS = (STORAGE_SHALLOW_ZARR, STORAGE_FULL_ZARR)
# Largest UTF-8 size of the inline ``Canonical_Biocodes`` value. The scripts
# replace any key/value pair above 1024 bytes by a CSV reference (see
# LARGE_FIELD_REFERENCE_TEMPLATE), so the biocode list is summarised well
# before that, to keep the pair searchable in OMERO.
MAX_INLINE_BIOCODES_BYTES = 800

# The scripts replace a key/value pair too large for the OMERO index by this
# text and attach the full value as a CSV file. A refresh must not re-inflate
# such a value, so core recognises the text with a pattern that matches what
# ``format_large_field_reference`` writes.
LARGE_FIELD_REFERENCE_TEMPLATE = (
    "Large field; full value in {csv_name} (utf8_bytes={size}; sha256={digest})")
LARGE_FIELD_REFERENCE_PATTERN = re.compile(
    r"Large field; full value in metadata_\S+\.csv "
    r"\(utf8_bytes=\d+; sha256=[0-9a-f]+\)")

# Evidence key -> view key, for the storage facts of a result.
STORAGE_EVIDENCE_LABELS = {
    "location": "Shallowing_Location",
    "tool_version": "Shallower_Tool_Version",
    "container": "Shallower_Container",
    "manifest": "Shallow_Manifest",
    "manifest_sha256": "Shallow_Manifest_SHA256",
    "report_sha256": "Shallower_Report_SHA256",
    "task_id": "Shallower_Task_ID",
    "job_id": "Shallower_Job_ID",
    "reason": "Storage_Outcome_Reason",
}


@dataclass
class MetadataAnnotation:
    """One key/value annotation: its namespace and its values."""

    namespace: str
    values: Dict[str, Any]


@dataclass
class MetadataChange:
    """A planned change; ``after`` is None to unlink the annotation."""

    before: MetadataAnnotation
    after: Optional[MetadataAnnotation]


class StorageEvidence(NamedTuple):
    """Newly recorded storage evidence on the latest version of a task."""

    task_id: str
    latest_task: Any
    evidence: Dict[str, Any]


# ------------------------------------------------------------------ #
# Contract with the scripts: reduced (large) fields
# ------------------------------------------------------------------ #

def format_large_field_reference(csv_name: str, size: int, digest: str) -> str:
    """Format the text that replaces a value too large for the OMERO index.

    Args:
        csv_name: File name of the CSV holding the full value.
        size: Size of the original value in UTF-8 bytes.
        digest: SHA-256 hex digest of the original value.

    Returns:
        The reference text, recognised by ``is_large_field_reference``.
    """
    return LARGE_FIELD_REFERENCE_TEMPLATE.format(
        csv_name=csv_name, size=size, digest=digest)


def is_large_field_reference(value: Any) -> bool:
    """Whether a stored value is a reference to a CSV file.

    Args:
        value: A stored annotation value.

    Returns:
        True if the value has the form ``format_large_field_reference`` writes.
    """
    return LARGE_FIELD_REFERENCE_PATTERN.fullmatch(str(value)) is not None


# ------------------------------------------------------------------ #
# Task selection and parameters
# ------------------------------------------------------------------ #

def is_internal_task(task: Any) -> bool:
    """Whether a task is coordination bookkeeping that the view omits.

    Args:
        task: A Task aggregate.

    Returns:
        True for a detached launcher task or a remote shallower task.
    """
    return (is_launcher_task(task)
            or task.task_name == REMOTE_SHALLOWER_TASK_NAME)


def qualifying_workflow_names(parameter_name: str) -> List[str]:
    """Return the workflow names a parameter name is qualified with.

    Args:
        parameter_name: A task parameter name, e.g. ``"cellpose | diameter"``.

    Returns:
        The workflow name in front of each separator found in the name.
    """
    return [parameter_name.split(separator, 1)[0]
            for separator in WORKFLOW_PARAM_SEPARATORS
            if separator in parameter_name]


def configured_workflow_names(params: Mapping[str, Any]) -> Set[str]:
    """Return the workflows that have qualified parameters in a task."""
    return {name for parameter_name in params
            for name in qualifying_workflow_names(parameter_name)}


def selected_workflow_names(
        params: Mapping[str, Any], task_name: str,
        configured: Set[str]) -> Optional[Iterable[str]]:
    """Return the selected workflows of an orchestration task, if known.

    Older batched orchestration tasks can contain the parameters of all
    configured workflows. Workflow-qualified parameters are only filtered when
    the selection is explicit, or can be derived from the boolean flags that
    the orchestration task stores per workflow.

    Args:
        params: Task parameters.
        task_name: Name of the task.
        configured: Workflows that have qualified parameters in the task.

    Returns:
        The selected workflow names, or None when the selection is unknown.
    """
    selected = params.get("workflows")
    if selected is None and task_name in ORCHESTRATION_TASK_NAMES:
        selected = [name for name in configured if params.get(name) is True]
    return selected


def is_parameter_of_unselected_workflow(
        parameter_name: str, selected: Set[str], configured: Set[str]) -> bool:
    """Whether a parameter is excluded when the workflow selection is known.

    Args:
        parameter_name: Name of the task parameter.
        selected: Selected workflow names.
        configured: All workflows that have qualified parameters in the task.

    Returns:
        True for descriptor parameters, the workflow flag and ``_Version``
        entry of an unselected workflow, and any qualified parameter of a
        workflow outside the selection.
    """
    if parameter_name.startswith(EXCLUDED_PARAM_PREFIXES):
        return True
    if any(name not in selected
           for name in qualifying_workflow_names(parameter_name)):
        return True
    if parameter_name in configured and parameter_name not in selected:
        return True
    if parameter_name.endswith(WORKFLOW_VERSION_SUFFIX):
        workflow_name = parameter_name[:-len(WORKFLOW_VERSION_SUFFIX)]
        return workflow_name in configured and workflow_name not in selected
    return False


def view_parameters(task: Any) -> Dict[str, Any]:
    """Return the task parameters that belong in the metadata view.

    Duplicated output settings and, where the selection is known, parameters
    of workflows that were configured but not selected are left out. They stay
    available in the event store and the full CSV.

    Args:
        task: A Task aggregate.

    Returns:
        The task parameters to render.
    """
    params = dict(task.params or {})
    params.pop(OUTPUT_SETTINGS_PARAM, None)
    configured = configured_workflow_names(params)
    selected = selected_workflow_names(params, task.task_name, configured)
    if not isinstance(selected, (list, tuple)):
        return params
    selected = set(selected)
    return {name: value for name, value in params.items()
            if not is_parameter_of_unselected_workflow(
                name, selected, configured)}


# ------------------------------------------------------------------ #
# Rendering
# ------------------------------------------------------------------ #

def validate_view_version(view_version: str) -> None:
    """Raise ValueError unless ``view_version`` is a supported view layout."""
    if view_version not in SUPPORTED_METADATA_VIEW_VERSIONS:
        raise ValueError(f"Unknown metadata view: {view_version}")


def load_aggregate(
        tracker: Any, aggregate_id: Any,
        aggregate_versions: Optional[Mapping[str, int]] = None) -> Any:
    """Load a workflow or task, at its pinned version when one is given.

    Args:
        tracker: A WorkflowTracker.
        aggregate_id: ID of the aggregate.
        aggregate_versions: Pinned version per aggregate ID, or None for the
            current state.

    Returns:
        The aggregate.

    Raises:
        KeyError: If the mapping has no version for this aggregate.
        ValueError: If the pinned version does not exist.
    """
    aggregate_id = UUID(str(aggregate_id))
    if aggregate_versions is None:
        return tracker.repository.get(aggregate_id)
    version = aggregate_versions[str(aggregate_id)]
    aggregate = tracker.repository.get(aggregate_id, version=version)
    if aggregate.version != version:
        raise ValueError(
            f"Aggregate version not found: {aggregate_id}/{version}")
    return aggregate


def load_visible_tasks(
        tracker: Any, workflow: Any,
        aggregate_versions: Optional[Mapping[str, int]]) -> List[Any]:
    """Load the tasks of a workflow that appear in the view.

    Args:
        tracker: A WorkflowTracker.
        workflow: The WorkflowRun aggregate.
        aggregate_versions: Pinned version per aggregate ID, or None.

    Returns:
        The non-internal tasks in workflow order.

    Raises:
        ValueError: If a pinned view has no version for a non-internal task.
    """
    tasks = []
    for task_id in workflow.tasks:
        if (aggregate_versions is not None
                and str(task_id) not in aggregate_versions):
            # Only an internal task may lack a pinned version. Whether a task
            # is internal is immutable creation data, so any version tells.
            task = tracker.repository.get(UUID(str(task_id)))
            if is_internal_task(task):
                logger.debug(f"Skipping internal task {task_id} without a "
                             f"pinned version")
                continue
            raise ValueError(f"Missing historical task version: {task_id}")
        task = load_aggregate(tracker, task_id, aggregate_versions)
        if is_internal_task(task):
            logger.debug(f"Omitting internal task {task_id} from the view")
            continue
        tasks.append(task)
    return tasks


def software_version(description: Any) -> str:
    """Extract the software version from a workflow description.

    The scripts store the version as the last line of the description
    (``"<description>\n<version>"``). Only that line is searched: a
    version number earlier in the description (for example "based on cellpose
    2.1.1") is not the software version. Workflows without a text description
    (core's own conversion workflow stores a number) have no version to find.

    Args:
        description: The workflow description, e.g. ``"Segments nuclei\n2.9.0"``.

    Returns:
        The first ``x.y.z`` version on the last line, else
        ``UNKNOWN_SOFTWARE_VERSION``.
    """
    if not isinstance(description, str) or not description.strip():
        logger.debug(f"No workflow description to read a software version "
                     f"from: {description!r}")
        return UNKNOWN_SOFTWARE_VERSION
    match = SOFTWARE_VERSION_PATTERN.search(
        description.strip().splitlines()[-1])
    if match is None:
        logger.warning(f"No software version on the last line of the "
                       f"workflow description {description!r}")
        return UNKNOWN_SOFTWARE_VERSION
    return match.group()


def make_annotation(
        namespace: str, values: Dict[str, Any], aggregate: Any,
        view_version: str, revision_fields: bool) -> MetadataAnnotation:
    """Return an annotation, with revision markers when they are enabled."""
    return MetadataAnnotation(namespace, with_revision(
        values, aggregate, view_version, revision_fields))


def with_revision(
        values: Dict[str, Any], aggregate: Any, view_version: str,
        revision_fields: bool) -> Dict[str, Any]:
    """Return ``values`` plus the view and aggregate version markers."""
    if not revision_fields:
        return values
    return {**values, "Metadata_View_Version": view_version,
            "Aggregate_Version": str(aggregate.version)}


def workflow_row_values(workflow: Any, workflow_id: Any,
                        tasks: Sequence[Any]) -> Dict[str, Any]:
    """Return the key/values of the workflow annotation."""
    return {
        "Workflow_ID": str(workflow_id),
        "Name": workflow.name,
        "Version": software_version(workflow.description),
        "Created_On": workflow._created_on.isoformat(),
        "Modified_On": workflow._modified_on.isoformat(),
        "Task_IDs": ", ".join(str(task._id) for task in tasks),
    }


def task_row_values(task: Any, workflow: Any, workflow_id: Any,
                    target_key: Optional[str]) -> Dict[str, Any]:
    """Return the key/values of a task annotation.

    Result tasks also get the storage facts recorded for ``target_key``.
    """
    values = {
        "Task_ID": str(task._id), "Workflow_ID": str(workflow_id),
        "Workflow_Name": workflow.name, "Name": task.task_name,
        "Version": task.task_version,
        "Created_On": task._created_on.isoformat(),
        "Modified_On": task._modified_on.isoformat(),
        "Status": task.status, "Input_Data": task.input_data,
        "Job_IDs": ", ".join(str(job_id) for job_id in task.job_ids),
    }
    values.update({f"{PARAM_PREFIX}{name}": str(value)
                   for name, value in view_parameters(task).items()})
    evidence = task.storage_provenance.get(target_key)
    if evidence and task.task_name in RESULT_TASK_NAMES:
        values.update(storage_metadata_view(evidence, target_key))
    return values


def job_row_values(task: Any, job_id: Any, workflow_id: Any) -> Dict[str, Any]:
    """Return the key/values of one job annotation of a task.

    Legacy ``v0`` layout: a task has one result message and one first result
    (command and environment), so every job of the task repeats them.
    """
    values = {"Job_ID": str(job_id), "Task_ID": str(task._id),
              "Workflow_ID": str(workflow_id),
              "Result_Message": task.result_message}
    first_result = task.results[0] if task.results else {}
    if "command" in first_result:
        values["Command"] = first_result["command"]
    values.update({f"{ENV_PREFIX}{name}": str(value)
                   for name, value in first_result.get("env", {}).items()})
    return values


def task_namespace(task: Any) -> str:
    """Return the namespace of a task annotation.

    Import tasks keep the Get Results namespace that result discovery uses.
    """
    name = (IMAGE_IMPORT_SCRIPT if task.task_name == IMPORT_RESULTS_SCRIPT
            else task.task_name)
    return f"{TASK_NAMESPACE_PREFIX}{name}"


def render_workflow_metadata(
        tracker: Any, workflow_id: Any, *,
        view_version: str = METADATA_VIEW_VERSION,
        aggregate_versions: Optional[Mapping[str, int]] = None,
        revision_fields: bool = True,
        target_key: Optional[str] = None) -> List[MetadataAnnotation]:
    """Return ordered namespace/value pairs without writing to any datastore.

    ``v0`` is the only supported view and retains legacy task/job fields,
    including job command, environment and result-message fields. It omits
    detached coordination tasks and duplicated output settings. Scientific
    false/zero values are retained. ``Version`` still means the software
    version.

    An explicit ``aggregate_versions`` mapping pins the workflow and every
    retained task independently. Missing versions fail instead of mixing a
    historical workflow with current task state.

    Args:
        tracker: A WorkflowTracker.
        workflow_id: ID of the workflow to render.
        view_version: View layout; only ``METADATA_VIEW_VERSION`` exists.
        aggregate_versions: Pinned version per aggregate ID, or None for the
            current state.
        revision_fields: Add ``Metadata_View_Version`` and
            ``Aggregate_Version``. Off reproduces the legacy layout.
        target_key: Result target (e.g. ``"Plate:123"``) whose storage facts
            to add to the import task.

    Returns:
        The workflow annotation, then for each task its annotation followed
        by one annotation per job.

    Raises:
        ValueError: For an unknown view or a version that cannot be found.
    """
    validate_view_version(view_version)
    workflow = load_aggregate(tracker, workflow_id, aggregate_versions)
    tasks = load_visible_tasks(tracker, workflow, aggregate_versions)

    rows = [make_annotation(
        NAMESPACE, workflow_row_values(workflow, workflow_id, tasks),
        workflow, view_version, revision_fields)]
    for task in tasks:
        namespace = task_namespace(task)
        rows.append(make_annotation(
            namespace, task_row_values(task, workflow, workflow_id, target_key),
            task, view_version, revision_fields))
        rows.extend(make_annotation(
            f"{namespace}{JOB_NAMESPACE_SUFFIX}",
            job_row_values(task, job_id, workflow_id),
            task, view_version, revision_fields)
            for job_id in task.job_ids)
    logger.debug(f"Rendered {len(rows)} metadata annotations for workflow "
                 f"{workflow_id} (view={view_version}, "
                 f"pinned={aggregate_versions is not None})")
    return rows


# ------------------------------------------------------------------ #
# Snapshot resolution
# ------------------------------------------------------------------ #

def belongs_to_workflow_view(row: MetadataAnnotation, workflow_id: Any) -> bool:
    """Whether an annotation is a view row of the given workflow."""
    in_view_namespace = (row.namespace == NAMESPACE
                         or row.namespace.startswith(TASK_NAMESPACE_PREFIX))
    return in_view_namespace and row.values.get("Workflow_ID") == str(workflow_id)


def versions_with_timestamp(tracker: Any, aggregate_id: str,
                            timestamp: Optional[str]) -> List[int]:
    """Find the versions of an aggregate that were last modified at a time.

    The modification time of version N is the timestamp of the event that
    produced it, so a single pass over the events finds every match, instead
    of replaying the aggregate once per version.

    Args:
        tracker: A WorkflowTracker.
        aggregate_id: ID of the workflow or task.
        timestamp: ISO timestamp from a stored ``Modified_On`` value.

    Returns:
        The matching versions (empty if none, several if ambiguous).
    """
    return [event.originator_version
            for event in tracker.events.get(UUID(aggregate_id))
            if event.timestamp.isoformat() == timestamp]


def snapshot_version(tracker: Any, aggregate_id: str,
                     values: Mapping[str, Any]) -> int:
    """Recover the aggregate version a stored view row was rendered from.

    New rows carry ``Aggregate_Version``. Legacy rows have no revision number
    and need a unique ``Modified_On`` match in the aggregate's history.

    Args:
        tracker: A WorkflowTracker.
        aggregate_id: ID of the workflow or task of the row.
        values: The stored key/values.

    Returns:
        The aggregate version.

    Raises:
        ValueError: If the snapshot is inconsistent, missing or ambiguous.
    """
    timestamp = values.get("Modified_On")
    if "Aggregate_Version" in values:
        version = int(values["Aggregate_Version"])
        aggregate = tracker.repository.get(UUID(aggregate_id), version=version)
        if (aggregate.version != version
                or timestamp != aggregate._modified_on.isoformat()):
            raise ValueError(f"Inconsistent snapshot: {aggregate_id}")
        return version
    matches = versions_with_timestamp(tracker, aggregate_id, timestamp)
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous snapshot: {aggregate_id}")
    return matches[0]


def resolve_metadata_versions(
        tracker: Any, workflow_id: Any,
        annotations: Iterable[MetadataAnnotation]) -> Dict[str, int]:
    """Recover exact aggregate versions from existing key/value snapshots.

    Job rows inherit the paired task snapshot, never the current task version.
    Missing or inconsistent snapshots are errors; callers must not guess.

    Args:
        tracker: A WorkflowTracker.
        workflow_id: ID of the workflow whose rows to resolve.
        annotations: Existing annotations of any namespace.

    Returns:
        The version per aggregate ID (the workflow and each task).

    Raises:
        ValueError: If a snapshot is inconsistent, ambiguous or conflicting,
            or the workflow has no snapshot.
    """
    versions = {}
    for row in annotations:
        if not belongs_to_workflow_view(row, workflow_id):
            continue
        if "Job_ID" in row.values:
            continue
        aggregate_id = row.values.get("Task_ID", str(workflow_id))
        version = snapshot_version(tracker, aggregate_id, row.values)
        if versions.get(aggregate_id, version) != version:
            raise ValueError(f"Conflicting snapshots: {aggregate_id}")
        versions[aggregate_id] = version
    if str(workflow_id) not in versions:
        raise ValueError("Missing workflow snapshot")
    logger.debug(f"Resolved snapshot versions for workflow {workflow_id}: "
                 f"{versions}")
    return versions


# ------------------------------------------------------------------ #
# Storage repair
# ------------------------------------------------------------------ #

def find_storage_evidence(
        tracker: Any, versions: Mapping[str, int],
        target_key: str) -> List[StorageEvidence]:
    """Find result tasks whose latest version has evidence for a target."""
    found = []
    for aggregate_id in versions:
        latest = tracker.repository.get(UUID(aggregate_id))
        if getattr(latest, "task_name", None) not in RESULT_TASK_NAMES:
            continue
        evidence = latest.storage_provenance.get(target_key)
        if evidence and storage_metadata_view(evidence, target_key):
            found.append(StorageEvidence(aggregate_id, latest, evidence))
    return found


def advance_import_task_to_storage_evidence(
        tracker: Any, versions: Mapping[str, int],
        target_key: Optional[str]) -> Dict[str, int]:
    """Pin the import task to the version that carries new storage evidence.

    Explicit storage repair advances only the import-task snapshot. Other
    snapshots stay pinned. Existing, different storage evidence in the pinned
    snapshot is never silently replaced.

    Args:
        tracker: A WorkflowTracker.
        versions: Pinned versions from ``resolve_metadata_versions``.
        target_key: Result target whose storage evidence to look for.

    Returns:
        A copy of ``versions`` with the import task advanced if needed.

    Raises:
        ValueError: Without a target, without exactly one import task that
            has evidence for it, or when the pinned snapshot holds different
            evidence.
    """
    if not target_key:
        raise ValueError("Storage repair requires a target_key")
    candidates = find_storage_evidence(tracker, versions, target_key)
    if len(candidates) != 1:
        raise ValueError(
            "Cannot identify unique import-task storage evidence for target")
    candidate = candidates[0]
    pinned_task = tracker.repository.get(
        UUID(candidate.task_id), version=versions[candidate.task_id])
    previous = pinned_task.storage_provenance.get(target_key)
    if previous and previous != candidate.evidence:
        raise ValueError(
            "Import snapshot already contains different storage evidence")
    if previous:
        logger.debug(f"Import task {candidate.task_id} already has the "
                     f"storage evidence for {target_key}; keeping its snapshot")
        return dict(versions)
    logger.info(f"Advancing import task {candidate.task_id} to version "
                f"{candidate.latest_task.version} for the storage evidence "
                f"of {target_key}")
    return {**versions, candidate.task_id: candidate.latest_task.version}


# ------------------------------------------------------------------ #
# Refresh planning
# ------------------------------------------------------------------ #

def annotation_identity(
        row: MetadataAnnotation) -> Tuple[str, Optional[str], Optional[str]]:
    """Return what identifies a view row: namespace, task ID and job ID."""
    return (row.namespace, row.values.get("Task_ID"), row.values.get("Job_ID"))


def stale_keys(tracker: Any, existing: Mapping[str, Any],
               rendered: Mapping[str, Any],
               versions: Mapping[str, int]) -> Set[str]:
    """Return stored keys that the current view no longer renders.

    ``Param_output_settings`` is always stale. Another ``Param_*`` key is
    stale if the pinned task has that parameter (the view now omits it); a key
    the task never had was added by a user and is kept.

    Args:
        tracker: A WorkflowTracker.
        existing: Stored key/values.
        rendered: Key/values the current view renders for this row.
        versions: Pinned aggregate versions.

    Returns:
        The keys to drop from the stored row.
    """
    stale = {f"{PARAM_PREFIX}{OUTPUT_SETTINGS_PARAM}"} & set(existing)
    task_id = existing.get("Task_ID")
    if task_id is None:
        return stale
    task_params = None
    for name in existing:
        if name.startswith(PARAM_PREFIX) and name not in rendered:
            if task_params is None:
                task = tracker.repository.get(
                    UUID(task_id), version=versions[task_id])
                task_params = task.params or {}
            if name[len(PARAM_PREFIX):] in task_params:
                stale.add(name)
    return stale


def merge_view_values(
        tracker: Any, existing: Mapping[str, Any],
        rendered: Mapping[str, Any],
        versions: Mapping[str, int]) -> Dict[str, Any]:
    """Merge the rendered view into a stored row.

    Unknown keys are preserved, keys the view omits are dropped and values
    already reduced to a CSV reference stay reduced.

    Args:
        tracker: A WorkflowTracker.
        existing: Stored key/values.
        rendered: Key/values the current view renders for this row.
        versions: Pinned aggregate versions.

    Returns:
        The key/values after the refresh.
    """
    stale = stale_keys(tracker, existing, rendered, versions)
    merged = {name: value for name, value in existing.items()
              if name not in stale}
    for name, value in rendered.items():
        if is_large_field_reference(existing.get(name, "")):
            logger.debug(f"Keeping the reduced value of {name}")
            continue
        merged[name] = value
    return merged


def plan_unexpected_row(
        tracker: Any, row: MetadataAnnotation, workflow_id: Any,
        identity: Tuple) -> MetadataChange:
    """Plan a stored view row that the current view no longer renders.

    Args:
        tracker: A WorkflowTracker.
        row: The stored annotation.
        workflow_id: ID of the workflow being refreshed.
        identity: The row's ``annotation_identity``.

    Returns:
        An unlink of the row if it belongs to an internal task.

    Raises:
        ValueError: If the row does not belong to an internal task.
    """
    task_id = row.values.get("Task_ID")
    if task_id and is_internal_task(tracker.repository.get(UUID(task_id))):
        logger.info(f"Unlinking annotation of internal task {task_id} "
                    f"({row.namespace}) in workflow {workflow_id}")
        return MetadataChange(row, None)
    raise ValueError(f"Unrecognized annotation identity in workflow "
                     f"{workflow_id}: {identity}")


def plan_metadata_refresh(
        tracker: Any, workflow_id: Any,
        annotations: Sequence[MetadataAnnotation], *,
        view_version: str = METADATA_VIEW_VERSION,
        target_key: Optional[str] = None,
        refresh_storage: bool = False) -> List[MetadataChange]:
    """Plan a snapshot-preserving update. None means unlink, not global delete.

    Unknown namespaces and extra keys are preserved. Missing task snapshots,
    duplicate identities and changed namespaces fail closed. Large fields that
    were already replaced by CSV references remain reduced.

    Explicit storage repair (``refresh_storage``) may advance the import-task
    snapshot to a version carrying newly recorded evidence for ``target_key``.
    Other snapshots stay pinned.

    Args:
        tracker: A WorkflowTracker.
        workflow_id: ID of the workflow to refresh.
        annotations: All annotations currently on the OMERO object.
        view_version: View layout; only ``METADATA_VIEW_VERSION`` exists.
        target_key: Result target for the storage facts.
        refresh_storage: Advance the import task to newly recorded storage
            evidence for ``target_key``.

    Returns:
        One change per given annotation, in order.

    Raises:
        ValueError: If the refresh cannot be planned safely.
    """
    validate_view_version(view_version)
    versions = resolve_metadata_versions(tracker, workflow_id, annotations)
    if refresh_storage:
        versions = advance_import_task_to_storage_evidence(
            tracker, versions, target_key)
    rendered = render_workflow_metadata(
        tracker, workflow_id, view_version=view_version,
        aggregate_versions=versions, target_key=target_key)
    expected_rows = {annotation_identity(row): row for row in rendered}

    changes = []
    seen_identities = set()
    for row in annotations:
        if not belongs_to_workflow_view(row, workflow_id):
            logger.debug(f"Keeping annotation outside the workflow view: "
                         f"{row.namespace}")
            changes.append(MetadataChange(row, row))
            continue
        identity = annotation_identity(row)
        if identity in seen_identities:
            raise ValueError(f"Duplicate annotation identity: {identity}")
        seen_identities.add(identity)
        expected_row = expected_rows.get(identity)
        if expected_row is None:
            changes.append(plan_unexpected_row(
                tracker, row, workflow_id, identity))
            continue
        merged = merge_view_values(
            tracker, row.values, expected_row.values, versions)
        changes.append(MetadataChange(
            row, MetadataAnnotation(row.namespace, merged)))

    missing_identities = set(expected_rows) - seen_identities
    if missing_identities:
        raise ValueError(
            f"Missing annotations for workflow {workflow_id}; refusing an "
            f"incomplete refresh: {sorted(map(str, missing_identities))}")
    return changes


# ------------------------------------------------------------------ #
# Storage facts
# ------------------------------------------------------------------ #

def storage_metadata_view(evidence: Mapping[str, Any],
                          target_key: Optional[str]) -> Dict[str, str]:
    """Return compact per-result storage facts; feature flags are not evidence.

    Args:
        evidence: Storage evidence recorded for one result target.
        target_key: The result target, e.g. ``"Plate:123"``.

    Returns:
        The view key/values, empty if the evidence names no known storage.
    """
    storage = evidence.get("storage")
    if storage not in STORAGE_FORMATS:
        return {}
    values = {"Storage_Target": target_key, "Storage_Format": storage,
              "Storage_Shallow": str(storage == STORAGE_SHALLOW_ZARR).lower()}
    for evidence_key, label in STORAGE_EVIDENCE_LABELS.items():
        if evidence.get(evidence_key) is not None:
            values[label] = str(evidence[evidence_key])
    codes = sorted(set(evidence.get("source_biocodes", [])))
    if codes:
        values["Canonical_Biocode_Count"] = str(len(codes))
        joined = ", ".join(codes)
        fits_inline = len(joined.encode("utf-8")) <= MAX_INLINE_BIOCODES_BYTES
        values["Canonical_Biocodes"] = (
            joined if fits_inline
            else f"{len(codes)} codes; see Shallow_Manifest")
    return values
