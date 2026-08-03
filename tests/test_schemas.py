import pytest
from pydantic import ValidationError

from axiom_slice.schemas import SliceRequest


def test_curated_overrides_are_strict() -> None:
    request = SliceRequest(overrides={"wall_loops": 4, "enable_support": True, "sparse_infill_density": 22.5})
    assert request.overrides["wall_loops"] == 4
    with pytest.raises(ValidationError):
        SliceRequest(overrides={"definitely_not_orca": "garbage"})
    with pytest.raises(ValidationError):
        SliceRequest(overrides={"wall_loops": "four"})
    with pytest.raises(ValidationError):
        SliceRequest(overrides={"support_threshold_angle": 91})


def test_profile_and_project_paths_are_not_accepted() -> None:
    with pytest.raises(ValidationError):
        SliceRequest(machine="../../etc/passwd")
    assert SliceRequest(project_name="My weird / project").project_name == "My-weird-project"
