from __future__ import annotations

import hashlib
import json
import os
import selectors
import shlex
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import Settings
from .schemas import SliceRequest
from .store import JobStore
from .thumbnail import extract_gcode_thumbnails, render_stl_thumbnails


class Cancelled(RuntimeError):
    pass


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
    thumbnails = extract_gcode_thumbnails(gcode, output_dir)
    if not thumbnails:
        stls = [path for path in inputs if path.suffix.lower() == ".stl"]
        if stls:
            try:
                thumbnails = render_stl_thumbnails(stls[0], output_dir)
            except ValueError as error:
                log("warning", f"Thumbnail fallback failed: {error}")
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
