import pytest

from biomero import constants


def test_qualitative_color_scheme_matches_blueprint():
    assert constants.QUALITATIVE_COLOR_SCHEME == (
        "#147EB3",
        "#29A634",
        "#D1980B",
        "#D33D17",
        "#9D3F9D",
        "#00A396",
        "#DB2C6F",
        "#8EB125",
        "#946638",
        "#7961DB",
    )


def test_resolve_workflow_color_is_deterministic_and_override_wins():
    first = "00000000-0000-0000-0000-000000000000"
    second = "00000000-0000-0000-0000-000000000001"

    assert constants.resolve_workflow_color("", first) == "#147EB3"
    assert constants.resolve_workflow_color(None, first) == "#147EB3"
    assert constants.resolve_workflow_color("", second) == "#29A634"
    assert constants.resolve_workflow_color("#d33d17", first) == "#D33D17"
    with pytest.raises(ValueError, match="#RRGGBB"):
        constants.resolve_workflow_color("red", first)


def test_shallow_zarr_feature_flag_is_forwardable_to_omero_scripts():
    assert (
        constants.slurm_env.BIOMERO_SHALLOW_ZARR
        == "BIOMERO_SHALLOW_ZARR"
    )


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
