from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PROJECT_RE = re.compile(r"[^A-Za-z0-9._-]+")

BedTypeName = Literal[
    "smooth-high-temp",
    "textured-pei",
    "smooth-cool",
    "textured-cool",
    "engineering",
    "supertack",
]

BED_TYPES: dict[str, dict[str, str]] = {
    "smooth-high-temp": {
        "label": "Smooth High Temp Plate",
        "orca": "High Temp Plate",
        "temperature_key": "hot_plate_temp_initial_layer",
    },
    "textured-pei": {
        "label": "Textured PEI Plate",
        "orca": "Textured PEI Plate",
        "temperature_key": "textured_plate_temp_initial_layer",
    },
    "smooth-cool": {
        "label": "Smooth Cool Plate",
        "orca": "Cool Plate",
        "temperature_key": "cool_plate_temp_initial_layer",
    },
    "textured-cool": {
        "label": "Textured Cool Plate",
        "orca": "Textured Cool Plate",
        "temperature_key": "textured_cool_plate_temp_initial_layer",
    },
    "engineering": {
        "label": "Engineering Plate",
        "orca": "Engineering Plate",
        "temperature_key": "eng_plate_temp_initial_layer",
    },
    "supertack": {
        "label": "Cool Plate (SuperTack)",
        "orca": "Supertack Plate",
        "temperature_key": "supertack_plate_temp_initial_layer",
    },
}


OVERRIDE_SPECS: dict[str, dict[str, Any]] = {
    "wall_loops": {"type": "integer", "minimum": 0, "maximum": 100, "label": "Wall loops"},
    "sparse_infill_density": {"type": "number", "minimum": 0, "maximum": 100, "label": "Infill density (%)"},
    "sparse_infill_pattern": {
        "type": "enum",
        "values": ["grid", "gyroid", "honeycomb", "crosshatch", "cubic", "adaptivecubic", "lightning", "rectilinear"],
        "label": "Infill pattern",
    },
    "top_shell_layers": {"type": "integer", "minimum": 0, "maximum": 100, "label": "Top shell layers"},
    "bottom_shell_layers": {"type": "integer", "minimum": 0, "maximum": 100, "label": "Bottom shell layers"},
    "ensure_vertical_shell_thickness": {
        "type": "enum",
        "values": ["ensure_all", "ensure_moderate", "none"],
        "label": "Vertical shell thickness",
    },
    "enable_support": {"type": "boolean", "label": "Enable supports"},
    "support_type": {
        "type": "enum",
        "values": ["normal(auto)", "tree(auto)", "normal(manual)", "tree(manual)"],
        "label": "Support type",
    },
    "support_on_build_plate_only": {"type": "boolean", "label": "Build-plate supports only"},
    "support_style": {
        "type": "enum",
        "values": ["default", "snug", "tree_slim", "tree_strong", "tree_hybrid", "organic"],
        "label": "Support style",
    },
    "support_threshold_angle": {"type": "number", "minimum": 0, "maximum": 90, "label": "Support threshold angle"},
    "tree_support_wall_count": {"type": "integer", "minimum": 0, "maximum": 2, "label": "Tree support walls"},
    "support_top_z_distance": {"type": "number", "minimum": 0, "maximum": 10, "label": "Support top Z distance"},
    "support_bottom_z_distance": {"type": "number", "minimum": 0, "maximum": 10, "label": "Support bottom Z distance"},
    "brim_type": {
        "type": "enum",
        "values": ["no_brim", "auto_brim", "brim_ears", "outer_only", "inner_only", "outer_and_inner"],
        "label": "Brim type",
    },
}


def validate_overrides(values: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(values) - set(OVERRIDE_SPECS))
    if unknown:
        raise ValueError(f"unsupported override(s): {', '.join(unknown)}")
    validated: dict[str, Any] = {}
    for name, value in values.items():
        spec = OVERRIDE_SPECS[name]
        kind = spec["type"]
        if kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false")
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
        elif kind == "enum" and value not in spec["values"]:
            raise ValueError(f"{name} must be one of: {', '.join(spec['values'])}")
        if kind in {"integer", "number"} and not spec["minimum"] <= value <= spec["maximum"]:
            raise ValueError(f"{name} must be between {spec['minimum']} and {spec['maximum']}")
        validated[name] = value
    return validated


class SliceRequest(BaseModel):
    project_name: str = "axiom-slice"
    machine: str = "Creality K1 (0.4 nozzle) - Custom"
    process: str = "0.16mm Optimal @Creality K1 (0.4 nozzle) - Custom"
    filament: str = "Sunlu PETG"
    bed_type: BedTypeName = "smooth-high-temp"
    auto_orient: bool = True
    export_3mf: bool = False
    overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("project_name")
    @classmethod
    def valid_project_name(cls, value: str) -> str:
        cleaned = PROJECT_RE.sub("-", value.strip()).strip(".-_")
        if not cleaned:
            raise ValueError("project_name must contain at least one letter or number")
        return cleaned[:96]

    @field_validator("machine", "process", "filament")
    @classmethod
    def valid_profile_name(cls, value: str) -> str:
        value = value.removesuffix(".json").strip()
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("profile names must be plain names")
        return value

    @field_validator("overrides")
    @classmethod
    def valid_override_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_overrides(value)


class PrintRequest(BaseModel):
    confirm: Literal[True]
