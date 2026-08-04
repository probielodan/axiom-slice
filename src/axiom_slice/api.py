from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__
from .config import Settings
from .printer import MoonrakerPrinter
from .profiles import ProfileError, ProfileResolver
from .schemas import BED_TYPES, OVERRIDE_SPECS, PrintRequest, SliceRequest
from .store import TERMINAL_STATES, JobStore

ALLOWED_SUFFIXES = {".stl", ".3mf"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare()
    store = JobStore(settings.database)
    resolver = ProfileResolver(settings.user_profiles, settings.system_profiles)
    printer = MoonrakerPrinter(settings.printer_url)
    app = FastAPI(title="Axiom Slice", version=__version__, docs_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.resolver = resolver
    app.state.printer = printer

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if not settings.token:
            raise HTTPException(503, "AXIOM_TOKEN is not configured")
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, settings.token):
            raise HTTPException(401, "invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    auth = [Depends(authenticate)]

    def public_job(job: dict[str, Any] | None) -> dict[str, Any]:
        if job is None:
            raise HTTPException(404, "job not found")
        result = job.get("result") or {}
        artifacts = result.get("artifacts", {})
        job["artifacts"] = {
            name: {**metadata, "url": f"/api/v1/jobs/{job['id']}/artifacts/{name}"}
            for name, metadata in artifacts.items()
        }
        return job

    @app.exception_handler(ProfileError)
    async def profile_error(_: Any, error: ProfileError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/profiles", dependencies=auth)
    async def profiles() -> dict[str, Any]:
        resolver.refresh()
        return {
            "profiles": resolver.options(),
            "compatible_profiles": resolver.compatible_options(),
            "bed_types": [{"value": value, "label": details["label"]} for value, details in BED_TYPES.items()],
            "defaults": SliceRequest().model_dump(exclude={"overrides", "auto_orient", "export_3mf", "project_name"}),
            "overrides": OVERRIDE_SPECS,
        }

    @app.post("/api/v1/jobs", dependencies=auth, status_code=202)
    async def create_job(
        request_text: Annotated[str, Form(alias="request")],
        models: Annotated[list[UploadFile], File()],
    ) -> dict[str, Any]:
        try:
            request = SliceRequest.model_validate_json(request_text)
        except ValidationError as error:
            raise HTTPException(422, json.loads(error.json())) from error
        if not models:
            raise HTTPException(422, "at least one model is required")
        resolved = resolver.resolve_request(request)
        job_id = uuid.uuid4().hex
        job_dir = settings.jobs_dir / job_id
        input_dir = job_dir / "input"
        input_dir.mkdir(parents=True)
        total = 0
        written: list[dict[str, Any]] = []
        try:
            used_names: set[str] = set()
            for upload in models:
                original = upload.filename or "model"
                filename = Path(original).name
                if filename != original or Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
                    raise HTTPException(422, f"unsupported or unsafe model filename: {original}")
                if filename in used_names:
                    raise HTTPException(422, f"duplicate model filename: {filename}")
                used_names.add(filename)
                destination = input_dir / filename
                size = 0
                with destination.open("wb") as output:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if total > settings.max_upload_bytes:
                            raise HTTPException(413, "uploaded models exceed the configured size limit")
                        output.write(chunk)
                if size == 0:
                    raise HTTPException(422, f"model is empty: {filename}")
                written.append({"name": filename, "size": size})
            (job_dir / "resolved-profiles.json").write_text(
                json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            payload = {"slice": request.model_dump(), "models": written}
            return public_job(store.create(job_id, payload))
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

    @app.get("/api/v1/jobs", dependencies=auth)
    async def list_jobs(limit: int = 50) -> dict[str, Any]:
        return {"jobs": [public_job(job) for job in store.list(max(1, min(limit, 200)))]}

    @app.get("/api/v1/jobs/{job_id}", dependencies=auth)
    async def get_job(job_id: str) -> dict[str, Any]:
        return public_job(store.get(job_id))

    @app.post("/api/v1/jobs/{job_id}/cancel", dependencies=auth)
    async def cancel_job(job_id: str) -> dict[str, Any]:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if not store.request_cancel(job_id):
            raise HTTPException(409, f"job cannot be cancelled from state {job['status']}")
        return public_job(store.get(job_id))

    @app.get("/api/v1/jobs/{job_id}/events", dependencies=auth)
    async def job_events(job_id: str, after: int = 0) -> StreamingResponse:
        if store.get(job_id) is None:
            raise HTTPException(404, "job not found")

        async def stream():
            sequence = after
            while True:
                events = store.events(job_id, sequence)
                for event in events:
                    sequence = event["sequence"]
                    yield f"id: {sequence}\nevent: log\ndata: {json.dumps(event)}\n\n"
                job = store.get(job_id)
                if job and job["status"] in TERMINAL_STATES and not store.events(job_id, sequence):
                    yield f"event: done\ndata: {json.dumps({'status': job['status']})}\n\n"
                    break
                yield ": keepalive\n\n"
                await asyncio.sleep(0.75)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/v1/jobs/{job_id}/artifacts/{name}", dependencies=auth)
    async def artifact(job_id: str, name: str) -> FileResponse:
        job = public_job(store.get(job_id))
        metadata = job["artifacts"].get(name)
        if not metadata:
            raise HTTPException(404, "artifact not found")
        job_dir = settings.jobs_dir / job_id
        path = (job_dir / metadata["path"]).resolve()
        if job_dir.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "artifact not found")
        return FileResponse(path, filename=name)

    @app.get("/api/v1/printer/status", dependencies=auth)
    async def printer_status() -> dict[str, Any]:
        try:
            return {"printer": app.state.printer.status()}
        except Exception as error:
            raise HTTPException(502, f"printer unavailable: {error}") from error

    @app.post("/api/v1/jobs/{job_id}/printer/upload", dependencies=auth)
    async def printer_upload(job_id: str) -> dict[str, Any]:
        job = public_job(store.get(job_id))
        if job["status"] != "succeeded":
            raise HTTPException(409, "only successful jobs can be uploaded")
        gcode_names = [name for name in job["artifacts"] if name.lower().endswith(".gcode")]
        if len(gcode_names) != 1:
            raise HTTPException(409, "job does not contain exactly one G-code artifact")
        metadata = job["artifacts"][gcode_names[0]]
        path = settings.jobs_dir / job_id / metadata["path"]
        try:
            upload = app.state.printer.upload(path)
        except Exception as error:
            raise HTTPException(502, f"printer upload failed: {error}") from error
        result = store.patch_result(job_id, {"printer_file": upload["filename"], "printer_upload": upload["response"]})
        store.event(job_id, f"Uploaded to printer as {upload['filename']}")
        return {"job_id": job_id, "filename": upload["filename"], "result": result}

    @app.post("/api/v1/jobs/{job_id}/printer/print", dependencies=auth)
    async def printer_print(job_id: str, _: PrintRequest) -> dict[str, Any]:
        job = public_job(store.get(job_id))
        filename = (job.get("result") or {}).get("printer_file")
        if not filename:
            raise HTTPException(409, "upload this job before starting the print")
        try:
            result = app.state.printer.start(filename)
        except Exception as error:
            raise HTTPException(502, f"printer start failed: {error}") from error
        store.event(job_id, f"Print started: {filename}", "warning")
        return {"job_id": job_id, "filename": filename, "printer": result}

    if settings.static_dir.is_dir():
        assets = settings.static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "API route not found")
            candidate = (settings.static_dir / path).resolve()
            if settings.static_dir.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(settings.static_dir / "index.html")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("axiom_slice.api:app", host="0.0.0.0", port=5000, proxy_headers=False)


if __name__ == "__main__":
    main()
