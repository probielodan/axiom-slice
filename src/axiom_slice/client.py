#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .schemas import BED_TYPES

DEFAULT_CONFIG = Path("~/.config/axiom-slice/client.env").expanduser()


def config_value(name: str, default: str = "") -> str:
    if value := os.environ.get(name):
        return value
    try:
        lines = DEFAULT_CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return default
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip("'\"")
    return default


class Client:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                content = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code}: {detail}") from error
        return json.loads(content) if content else None

    def multipart(self, path: str, request_data: dict[str, Any], models: list[Path]) -> Any:
        boundary = f"axiom-{uuid.uuid4().hex}"
        chunks: list[bytes] = []

        def field(name: str, value: bytes, filename: str | None = None, content_type: str | None = None) -> None:
            chunks.append(f"--{boundary}\r\n".encode())
            disposition = f'Content-Disposition: form-data; name="{name}"'
            if filename:
                disposition += f'; filename="{filename}"'
            chunks.append((disposition + "\r\n").encode())
            if content_type:
                chunks.append(f"Content-Type: {content_type}\r\n".encode())
            chunks.append(b"\r\n" + value + b"\r\n")

        field("request", json.dumps(request_data).encode())
        for model in models:
            field(
                "models",
                model.read_bytes(),
                model.name,
                mimetypes.guess_type(model.name)[0] or "application/octet-stream",
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return self.request("POST", path, b"".join(chunks), f"multipart/form-data; boundary={boundary}")

    def download(self, job_id: str, artifact: str, destination: Path) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/jobs/{job_id}/artifacts/{urllib.parse.quote(artifact)}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Submit and manage Axiom Slice jobs")
    root.add_argument("--url", default=config_value("AXIOM_SLICE_URL", "http://fedora.local:5000"))
    root.add_argument("--token", default=config_value("AXIOM_SLICE_TOKEN"), help=argparse.SUPPRESS)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("profiles", help="List available curated profiles and overrides")
    submit = commands.add_parser("submit", help="Submit STL/3MF models for slicing")
    submit.add_argument("models", nargs="+", type=Path)
    submit.add_argument("--name", default="axiom-slice")
    submit.add_argument("--machine", default="Creality K1 (0.4 nozzle) - Custom")
    submit.add_argument("--process", default="0.16mm Optimal @Creality K1 (0.4 nozzle) - Custom")
    submit.add_argument("--filament", default="Sunlu PETG")
    submit.add_argument("--bed-type", choices=BED_TYPES, default="smooth-high-temp")
    submit.add_argument("--no-auto-orient", action="store_true")
    submit.add_argument("--export-3mf", action="store_true")
    submit.add_argument("--set", action="append", default=[], metavar="NAME=JSON_VALUE")
    status = commands.add_parser("status", help="Show one job")
    status.add_argument("job_id")
    wait = commands.add_parser("wait", help="Wait until one job finishes")
    wait.add_argument("job_id")
    wait.add_argument("--interval", type=float, default=2)
    listing = commands.add_parser("jobs", help="List recent jobs")
    listing.add_argument("--limit", type=int, default=20)
    cancel = commands.add_parser("cancel", help="Cancel a queued or running job")
    cancel.add_argument("job_id")
    download = commands.add_parser("download", help="Download one named artifact")
    download.add_argument("job_id")
    download.add_argument("artifact")
    download.add_argument("--output", type=Path)
    upload = commands.add_parser("upload", help="Upload successful G-code to the configured printer")
    upload.add_argument("job_id")
    return root


def main() -> None:
    args = parser().parse_args()
    if not args.token:
        raise SystemExit(f"Set AXIOM_SLICE_TOKEN or add it to {DEFAULT_CONFIG}")
    client = Client(args.url, args.token)
    if args.command == "profiles":
        result = client.request("GET", "/api/v1/profiles")
    elif args.command == "submit":
        missing = [str(path) for path in args.models if not path.is_file()]
        if missing:
            raise SystemExit(f"Missing model(s): {', '.join(missing)}")
        overrides: dict[str, Any] = {}
        for assignment in args.set:
            name, separator, raw = assignment.partition("=")
            if not separator:
                raise SystemExit(f"Invalid override {assignment!r}; use NAME=JSON_VALUE")
            try:
                overrides[name] = json.loads(raw)
            except json.JSONDecodeError as error:
                raise SystemExit(f"Invalid JSON value for {name}: {error}") from error
        payload = {
            "project_name": args.name,
            "machine": args.machine,
            "process": args.process,
            "filament": args.filament,
            "bed_type": args.bed_type,
            "auto_orient": not args.no_auto_orient,
            "export_3mf": args.export_3mf,
            "overrides": overrides,
        }
        result = client.multipart("/api/v1/jobs", payload, args.models)
    elif args.command == "status":
        result = client.request("GET", f"/api/v1/jobs/{args.job_id}")
    elif args.command == "jobs":
        result = client.request("GET", f"/api/v1/jobs?limit={args.limit}")
    elif args.command == "cancel":
        result = client.request("POST", f"/api/v1/jobs/{args.job_id}/cancel")
    elif args.command == "upload":
        result = client.request("POST", f"/api/v1/jobs/{args.job_id}/printer/upload")
    elif args.command == "download":
        destination = args.output or Path(args.artifact)
        client.download(args.job_id, args.artifact, destination)
        result = {"downloaded": str(destination.resolve())}
    else:
        while True:
            result = client.request("GET", f"/api/v1/jobs/{args.job_id}")
            print(f"{result['status']}: {args.job_id}", file=sys.stderr)
            if result["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(args.interval)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
