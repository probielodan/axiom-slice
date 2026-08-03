from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class PrinterError(RuntimeError):
    pass


class MoonrakerPrinter:
    def __init__(self, base_url: str, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def status(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/printer/objects/query",
                params={"print_stats": "", "virtual_sdcard": ""},
            )
            response.raise_for_status()
            return response.json().get("result", {}).get("status", {})

    def upload(self, gcode: Path, remote_dir: str = "Axiom Slice") -> dict[str, Any]:
        with gcode.open("rb") as source, httpx.Client(timeout=max(self.timeout, 120)) as client:
            response = client.post(
                f"{self.base_url}/server/files/upload",
                data={"root": "gcodes", "path": remote_dir},
                files={"file": (gcode.name, source, "text/x.gcode")},
            )
            response.raise_for_status()
            result = response.json().get("result", {})
        item = result.get("item", result)
        remote = item.get("path") or f"{remote_dir}/{gcode.name}"
        return {"filename": remote, "response": result}

    def start(self, filename: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/printer/print/start", params={"filename": filename})
            response.raise_for_status()
            return response.json().get("result", {})
