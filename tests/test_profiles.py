from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiom_slice.profiles import ProfileError, ProfileResolver
from axiom_slice.schemas import SliceRequest


def resolver(root: Path) -> ProfileResolver:
    return ProfileResolver(root / "user/default", root / "system")


def request() -> SliceRequest:
    return SliceRequest(machine="Custom K1", process="Custom process", filament="Custom PETG")


def test_lazy_resolution_merges_without_writing_source(profile_root: Path) -> None:
    before = {path: path.read_bytes() for path in profile_root.rglob("*.json")}
    resolved = resolver(profile_root).resolve_request(request())
    assert resolved["machine"]["nozzle"] == "0.6"
    assert resolved["process"]["wall_loops"] == "3"
    assert resolved["filament"]["temp"] == ["260"]
    assert resolved["chains"]["machine"] == ["Custom K1", "Base K1"]
    assert {path: path.read_bytes() for path in profile_root.rglob("*.json")} == before


def test_missing_parent_is_a_hard_error(profile_root: Path) -> None:
    path = profile_root / "user/default/process/Custom process.json"
    data = json.loads(path.read_text())
    data["inherits"] = "Definitely missing"
    path.write_text(json.dumps(data))
    with pytest.raises(ProfileError, match="missing process profile"):
        resolver(profile_root).resolve_request(request())


def test_cycle_is_a_hard_error(profile_root: Path) -> None:
    base = profile_root / "system/Creality/process/base-process.json"
    data = json.loads(base.read_text())
    data["inherits"] = "Custom process"
    base.write_text(json.dumps(data))
    with pytest.raises(ProfileError, match="cycle"):
        resolver(profile_root).resolve_request(request())


def test_incompatible_profile_is_rejected(profile_root: Path) -> None:
    path = profile_root / "system/Creality/filament/base-filament.json"
    data = json.loads(path.read_text())
    data["compatible_printers"] = ["Some other printer"]
    path.write_text(json.dumps(data))
    with pytest.raises(ProfileError, match="not compatible"):
        resolver(profile_root).resolve_request(request())
