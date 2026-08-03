from pathlib import Path

from fastapi.testclient import TestClient

from axiom_slice.api import create_app
from axiom_slice.config import Settings


def settings(tmp_path: Path, profile_root: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        profile_root=profile_root,
        static_dir=tmp_path / "missing-static",
        token="test-token",
        orca_bin=Path("/bin/false"),
        orca_version="test",
        orca_digest="sha256:test",
        printer_url="http://127.0.0.1:9",
        max_upload_bytes=1024,
        poll_seconds=0.01,
    )


def test_auth_profile_listing_and_safe_job_upload(tmp_path: Path, profile_root: Path) -> None:
    app = create_app(settings(tmp_path, profile_root))
    client = TestClient(app)
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/profiles").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    profile_response = client.get("/api/v1/profiles", headers=headers)
    assert profile_response.status_code == 200
    request = {"machine": "Custom K1", "process": "Custom process", "filament": "Custom PETG"}
    response = client.post(
        "/api/v1/jobs",
        headers=headers,
        data={"request": __import__("json").dumps(request)},
        files={"models": ("cube.stl", b"solid cube\nendsolid cube\n", "model/stl")},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "queued"
    assert (
        client.post(
            "/api/v1/jobs",
            headers=headers,
            data={"request": __import__("json").dumps(request)},
            files={"models": ("../escape.stl", b"x", "model/stl")},
        ).status_code
        == 422
    )

    class FakePrinter:
        def upload(self, path: Path):
            assert path.suffix == ".gcode"
            return {"filename": f"Axiom Slice/{path.name}", "response": {"ok": True}}

        def start(self, filename: str):
            return {"started": filename}

    job_id = job["id"]
    output = app.state.settings.jobs_dir / job_id / "output"
    output.mkdir()
    gcode = output / "cube.gcode"
    gcode.write_text("G28\n")
    app.state.store.finish(
        job_id, "succeeded", {"artifacts": {"cube.gcode": {"path": "output/cube.gcode", "size": 4, "sha256": "test"}}}
    )
    app.state.printer = FakePrinter()
    assert client.post(f"/api/v1/jobs/{job_id}/printer/upload", headers=headers).status_code == 200
    assert client.post(f"/api/v1/jobs/{job_id}/printer/print", headers=headers).status_code == 422
    assert (
        client.post(f"/api/v1/jobs/{job_id}/printer/print", headers=headers, json={"confirm": True}).status_code == 200
    )
