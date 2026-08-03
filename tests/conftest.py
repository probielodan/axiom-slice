from __future__ import annotations

import json
from pathlib import Path

import pytest


def write_profile(root: Path, location: str, data: dict) -> Path:
    path = root / location
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def profile_root(tmp_path: Path) -> Path:
    root = tmp_path / "OrcaSlicer"
    write_profile(
        root, "system/Creality/machine/base-machine.json", {"name": "Base K1", "type": "machine", "nozzle": "0.4"}
    )
    write_profile(
        root,
        "system/Creality/process/base-process.json",
        {"name": "Base process", "type": "process", "compatible_printers": ["Base K1"], "wall_loops": "2"},
    )
    write_profile(
        root,
        "system/Creality/filament/base-filament.json",
        {"name": "Base PETG", "type": "filament", "compatible_printers": ["Base K1"], "temp": ["250"]},
    )
    write_profile(
        root, "user/default/machine/Custom K1.json", {"name": "Custom K1", "inherits": "Base K1", "nozzle": "0.6"}
    )
    write_profile(
        root,
        "user/default/process/Custom process.json",
        {"name": "Custom process", "inherits": "Base process", "wall_loops": "3"},
    )
    write_profile(
        root,
        "user/default/filament/Custom PETG.json",
        {"name": "Custom PETG", "inherits": "Base PETG", "temp": ["260"]},
    )
    return root
