from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shlex
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import Settings
from .schemas import BED_TYPES, SliceRequest
from .store import JobStore
from .thumbnail import embed_gcode_thumbnails, extract_gcode_thumbnails, render_stl_thumbnails


class Cancelled(RuntimeError):
    pass


PRINT_TIME_RE = re.compile(r"^;\s*estimated printing time \(normal mode\)\s*=\s*(.+?)\s*$", re.MULTILINE)
BED_TYPE_RE = re.compile(r"^;\s*curr_bed_type\s*=\s*(.+?)\s*$", re.MULTILINE)
BED_TEMPERATURE_RE = re.compile(r"^;\s*first_layer_bed_temperature\s*=\s*([0-9.]+)\s*$", re.MULTILINE)
PLATE_NAME_FALLBACK_RE = re.compile(
    r'\{\s*plate_name\s*!=\s*""\s*\?\s*plate_name\s*:\s*input_filename_base\s*\}'
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _orca_value(name: str, value: Any) -> Any:
    if isinstance(value, bool):
        return "1" if value else "0"
    if name == "sparse_infill_density":
        return f"{value}%"
    return str(value)


def _short_print_time(value: str) -> str:
    match = re.fullmatch(
        r"\s*(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+(?:\.\d+)?)s)?\s*", value
    )
    if not match or not any(match.groups()):
        return re.sub(r"\s+", "", value)
    days, hours, minutes = (int(part or 0) for part in match.groups()[:3])
    float_seconds = float(match.group(4) or 0)
    seconds = int(float_seconds)
    if days + hours > 0 and seconds >= 30:
        minutes += 1
        if minutes == 60:
            minutes = 0
            hours += 1
            if hours == 24:
                hours = 0
                days += 1
    if days:
        return f"{days}d{hours}h{minutes}m"
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{seconds}s"
    if seconds:
        return f"{seconds}s"
    if float_seconds:
        return "<1s"
    return "0s"


def _gcode_tail(gcode: Path) -> str:
    with gcode.open("rb") as source:
        source.seek(max(0, gcode.stat().st_size - 2 * 1024 * 1024))
        return source.read().decode("utf-8", errors="replace")


def _estimated_print_time(gcode: Path) -> str | None:
    match = PRINT_TIME_RE.search(_gcode_tail(gcode))
    return _short_print_time(match.group(1)) if match else None


def _expected_bed_temperature(resolved: dict[str, Any], request: SliceRequest) -> int:
    bed = BED_TYPES[request.bed_type]
    value = resolved["filament"].get(bed["temperature_key"])
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        temperature = int(float(value))
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            f"{request.filament} has no valid first-layer temperature for {bed['label']}"
        ) from error
    if temperature <= 0:
        raise RuntimeError(f"{request.filament} does not support {bed['label']}")
    return temperature


def _verify_bed_selection(gcode: Path, request: SliceRequest, expected_temperature: int) -> None:
    bed = BED_TYPES[request.bed_type]
    tail = _gcode_tail(gcode)
    actual_type = BED_TYPE_RE.search(tail)
    actual_temperature = BED_TEMPERATURE_RE.search(tail)
    if not actual_type or actual_type.group(1) != bed["orca"]:
        found = actual_type.group(1) if actual_type else "missing"
        raise RuntimeError(f"Orca selected {found!r} instead of {bed['orca']!r}")
    if not actual_temperature or float(actual_temperature.group(1)) != expected_temperature:
        found = actual_temperature.group(1) if actual_temperature else "missing"
        raise RuntimeError(f"Orca generated bed temperature {found!r}; expected {expected_temperature} °C")


def _profile_output_name(
    gcode: Path, inputs: list[Path], resolved: dict[str, Any], log: Callable[[str, str], None]
) -> str:
    input_base = inputs[0].stem
    process = resolved["process"]
    filament = resolved["filament"].get("filament_type", ["filament"])
    filament_type = str(filament[0] if isinstance(filament, list) and filament else filament)
    print_time = _estimated_print_time(gcode)
    if not print_time:
        print_time = "unknown-time"
        log("warning", "Could not read Orca's estimated print time for the output filename")
    template = str(process.get("filename_format") or "{input_filename_base}.gcode")
    rendered = PLATE_NAME_FALLBACK_RE.sub(input_base, template)
    replacements = {
        "{plate_name}": "",
        "{input_filename_base}": input_base,
        "{filament_type[initial_tool]}": filament_type,
        "{filament_type[0]}": filament_type,
        "{print_time}": print_time,
        "{normal_print_time}": print_time,
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    if re.search(r"\{[^{}]+\}", rendered):
        log("warning", f"Unsupported placeholder in filename format: {template}; using the input filename")
        rendered = f"{input_base}.gcode"
    rendered = re.sub(r'[\x00-\x1f/\\]+', "_", rendered).strip(" .")
    if not rendered:
        rendered = f"{input_base}.gcode"
    if not rendered.lower().endswith(".gcode"):
        rendered += ".gcode"
    return rendered


def _add_fallback_thumbnails(
    gcode: Path, inputs: list[Path], output_dir: Path, log: Callable[[str, str], None]
) -> dict[str, Path]:
    stls = [path for path in inputs if path.suffix.lower() == ".stl"]
    if not stls:
        return {}
    if len(stls) > 1:
        log(
            "warning",
            "Orca produced no thumbnail; refusing to show a misleading single-model fallback for a multi-model job",
        )
        return {}
    try:
        rendered = render_stl_thumbnails(stls[0], output_dir)
        embed_gcode_thumbnails(gcode, rendered)
        thumbnails = extract_gcode_thumbnails(gcode, output_dir)
        if not thumbnails:
            raise ValueError("embedded fallback thumbnails could not be verified")
        log("info", f"Embedded {len(thumbnails)} fallback thumbnails in G-code")
        return thumbnails
    except ValueError as error:
        log("warning", f"Thumbnail fallback failed: {error}")
        return {}


def prepare_profiles(job_dir: Path, resolved: dict[str, Any], request: SliceRequest) -> dict[str, Path]:
    profile_dir = job_dir / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profiles = {kind: dict(resolved[kind]) for kind in ("machine", "process", "filament")}
    for kind, profile in profiles.items():
        chain = resolved["chains"][kind]
        if len(chain) > 1:
            profile["inherits"] = chain[1]
    for name, value in request.overrides.items():
        profiles["process"][name] = _orca_value(name, value)
    paths: dict[str, Path] = {}
    for kind, profile in profiles.items():
        path = profile_dir / f"{kind}.json"
        path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths[kind] = path
    (profile_dir / "inheritance.json").write_text(
        json.dumps(resolved["chains"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return paths


def build_command(
    settings: Settings, job_dir: Path, request: SliceRequest, inputs: list[Path], profiles: dict[str, Path]
) -> list[str]:
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [str(settings.orca_bin), "--allow-newer-file", "--allow-rotations"]
    if request.auto_orient:
        command += ["--orient", "1"]
    command += [
        "--arrange",
        "1",
        "--ensure-on-bed",
        "--slice",
        "0",
        "--curr-bed-type",
        BED_TYPES[request.bed_type]["orca"],
        "--debug",
        "2",
        "--logfile",
        str(job_dir / "orca-native.log"),
        "--datadir",
        str(settings.profile_root),
        "--load-settings",
        f"{profiles['machine']};{profiles['process']}",
        "--load-filaments",
        str(profiles["filament"]),
        "--outputdir",
        str(output_dir),
    ]
    if request.export_3mf:
        command += ["--export-3mf", f"{request.project_name}.3mf"]
    return command + [str(path) for path in inputs]


def run_job(
    settings: Settings, store: JobStore, job: dict[str, Any], log: Callable[[str, str], None]
) -> dict[str, Any]:
    job_id = job["id"]
    job_dir = settings.jobs_dir / job_id
    request = SliceRequest.model_validate(job["request"]["slice"])
    resolved = json.loads((job_dir / "resolved-profiles.json").read_text(encoding="utf-8"))
    expected_bed_temperature = _expected_bed_temperature(resolved, request)
    bed = BED_TYPES[request.bed_type]
    log("info", f"Build plate: {bed['label']}; profile target: {expected_bed_temperature} °C")
    inputs = sorted((job_dir / "input").iterdir())
    profiles = prepare_profiles(job_dir, resolved, request)
    command = build_command(settings, job_dir, request, inputs, profiles)
    command_display = shlex.join(command)
    (job_dir / "command.txt").write_text(command_display + "\n", encoding="utf-8")
    log("info", f"OrcaSlicer {settings.orca_version} starting")
    log("debug", command_display)
    started = time.monotonic()
    environment = os.environ.copy()
    environment.setdefault("HOME", "/tmp/axiom-slice-home")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=environment,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output_lines: list[str] = []
    while process.poll() is None:
        if store.status(job_id) == "cancel_requested":
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise Cancelled("Job cancelled")
        for key, _ in selector.select(timeout=0.25):
            line = key.fileobj.readline()
            if line:
                clean = line.rstrip("\r\n")
                output_lines.append(clean)
                log("orca", clean)
    remainder = process.stdout.read()
    if remainder:
        for line in remainder.splitlines():
            output_lines.append(line)
            log("orca", line)
    duration = round(time.monotonic() - started, 3)
    (job_dir / "orca.log").write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    if process.returncode:
        detail = ""
        result_path = job_dir / "output" / "result.json"
        if result_path.exists():
            with suppress(OSError, json.JSONDecodeError):
                detail = json.loads(result_path.read_text(encoding="utf-8")).get("error_string", "")
        raise RuntimeError(f"OrcaSlicer exited {process.returncode}{f': {detail}' if detail else ''}")
    output_dir = job_dir / "output"
    gcodes = sorted(output_dir.rglob("*.gcode"), key=lambda path: path.stat().st_mtime)
    if not gcodes:
        raise RuntimeError("OrcaSlicer succeeded but produced no G-code")
    gcode = gcodes[-1]
    _verify_bed_selection(gcode, request, expected_bed_temperature)
    log("info", f"Verified {bed['label']} at {expected_bed_temperature} °C")
    named_gcode = output_dir / _profile_output_name(gcode, inputs, resolved, log)
    if gcode != named_gcode:
        gcode = gcode.replace(named_gcode)
    log("info", f"Named G-code from profile format: {gcode.name}")
    thumbnails = extract_gcode_thumbnails(gcode, output_dir)
    if not thumbnails:
        thumbnails = _add_fallback_thumbnails(gcode, inputs, output_dir, log)
    artifacts: dict[str, dict[str, Any]] = {}
    candidates = [
        gcode,
        job_dir / "orca.log",
        job_dir / "orca-native.log",
        job_dir / "command.txt",
        job_dir / "resolved-profiles.json",
        *thumbnails.values(),
    ]
    candidates.extend(output_dir.rglob("*.3mf"))
    for path in candidates:
        if path.exists() and path.is_file():
            key = path.name
            artifacts[key] = {
                "path": str(path.relative_to(job_dir)),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
    manifest = {
        "job_id": job_id,
        "duration_seconds": duration,
        "orca": {"version": settings.orca_version, "digest": settings.orca_digest, "exit_code": process.returncode},
        "inputs": [{"name": path.name, "size": path.stat().st_size, "sha256": sha256(path)} for path in inputs],
        "artifacts": artifacts,
    }
    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts[manifest_path.name] = {
        "path": manifest_path.name,
        "size": manifest_path.stat().st_size,
        "sha256": sha256(manifest_path),
    }
    manifest["artifacts"] = artifacts
    return manifest
