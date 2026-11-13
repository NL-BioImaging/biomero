import pytest

from biomero import constants


def test_shallow_zarr_feature_flag_is_forwardable_to_omero_scripts():
    assert (
        constants.slurm_env.BIOMERO_SHALLOW_ZARR
        == "BIOMERO_SHALLOW_ZARR"
    )


def test_detached_worker_environment_names_are_stable():
    assert (
        constants.slurm_env.BIOMERO_DETACHED_WORKFLOWS
        == "BIOMERO_DETACHED_WORKFLOWS"
    )
    assert (
        constants.slurm_env.BIOMERO_MAX_ACTIVE_WORKFLOWS
        == "BIOMERO_MAX_ACTIVE_WORKFLOWS"
    )
    assert (
        constants.slurm_env.BIOMERO_SUPERVISOR_POLL_SECONDS
        == "BIOMERO_SUPERVISOR_POLL_SECONDS"
    )
    assert (
        constants.slurm_env.BIOMERO_SUPERVISOR_STARTUP_GRACE_SECONDS
        == "BIOMERO_SUPERVISOR_STARTUP_GRACE_SECONDS"
    )


def test_processed_data_folder_environment_name_is_stable():
    assert constants.slurm_env.PROCESSED_DATA_FOLDER == "PROCESSED_DATA_FOLDER"


def test_shallow_reconstruction_transfer_input_is_stable():
    assert (
        constants.transfer.RECONSTRUCT_SHALLOW_ZARR
        == "Reconstruct_Shallow_Zarr"
    )


def test_plate_label_preview_result_inputs_are_stable():
    assert (
        constants.results.IMPORT_PLATE_LABEL_PREVIEW
        == "Import_Plate_Label_Preview"
    )
    assert (
        constants.results.PLATE_LABEL_PREVIEW_NAME
        == "Plate_Label_Preview_Name"
    )


def test_file_output_target_contract_is_shared_by_workflow_and_results_scripts():
    assert (
        constants.workflow.OUTPUT_ATTACH_FILE_OUTPUTS_TARGET
        == "5a) File annotation destination"
    )
    assert (
        constants.results.OUTPUT_ATTACH_FILE_OUTPUTS_TARGET
        == "File output destination"
    )
    assert constants.file_output_targets.LEGACY == "legacy_input_container"
    assert constants.file_output_targets.AUTO == "auto"
    assert constants.file_output_targets.RESULT_DESTINATION == "result_destination"
    assert constants.file_output_targets.INPUT_CONTAINER == "input_container"
    assert constants.file_output_targets.INPUT_PARENT == "input_parent"
    assert constants.file_output_targets.USER_VALUES == (
        "auto",
        "result_destination",
        "input_container",
        "input_parent",
    )


# The colour helpers shipped in stable 2.8.x and are called by stable scripts;
# they stay available, deprecated, until the scripts release that drops them.

def test_deprecated_workflow_color_helper_still_works_and_warns():
    first = "00000000-0000-0000-0000-000000000000"
    second = "00000000-0000-0000-0000-000000000001"

    with pytest.warns(DeprecationWarning, match="resolve_workflow_color"):
        resolve = constants.resolve_workflow_color
    assert resolve("", first) == "#147EB3"
    assert resolve(None, first) == "#147EB3"
    assert resolve("", second) == "#29A634"
    assert resolve("#d33d17", first) == "#D33D17"
    with pytest.raises(ValueError, match="#RRGGBB"):
        resolve("red", first)


def test_deprecated_color_scheme_still_matches_blueprint_and_warns():
    with pytest.warns(DeprecationWarning, match="QUALITATIVE_COLOR_SCHEME"):
        scheme = constants.QUALITATIVE_COLOR_SCHEME
    assert scheme == (
        "#147EB3", "#29A634", "#D1980B", "#D33D17", "#9D3F9D",
        "#00A396", "#DB2C6F", "#8EB125", "#946638", "#7961DB")


def test_deprecated_names_can_be_imported_from_the_module():
    with pytest.warns(DeprecationWarning):
        from biomero.constants import resolve_workflow_color  # noqa: F401


def test_unknown_constant_is_an_attribute_error():
    with pytest.raises(AttributeError):
        constants.NO_SUCH_CONSTANT
