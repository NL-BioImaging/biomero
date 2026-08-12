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
