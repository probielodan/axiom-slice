# Axiom Slice

Axiom Slice turns the Fedora workstation's curated Creality K1 OrcaSlicer profiles into a private slicing service. It provides a human web UI and a constrained agent CLI without giving an agent shell or SSH access to the workstation.

## Boundary

- The API accepts only STL/3MF models, named user profiles, and a curated override schema.
- Profiles are inherited and flattened in memory for each job. Source Orca profiles are mounted read-only and never rewritten.
- The persistent worker slices one job at a time with no network and stores reproducible artifacts in a durable SQLite-backed queue.
- Every job retains its inputs, request, resolved profiles, inheritance chains, command, Orca log, checksums, G-code, thumbnails, and optional 3MF.
- Build-plate selection uses Orca's `--curr-bed-type`; each filament profile remains authoritative for that plate's temperatures, and the worker verifies the generated G-code.
- The API can upload successful G-code to the configured Moonraker printer. The web UI can start a print only after an explicit confirmation.
- The agent CLI intentionally supports upload but has no print command. Printer motion and print authorization remain separate concerns.

## API

All routes except health and static UI assets require `Authorization: Bearer <token>`.

- `GET /api/v1/health`
- `GET /api/v1/profiles`
- `POST /api/v1/jobs` with multipart `models` and JSON `request`
- `GET /api/v1/jobs` and `GET /api/v1/jobs/{id}`
- `GET /api/v1/jobs/{id}/events` (SSE)
- `POST /api/v1/jobs/{id}/cancel`
- `GET /api/v1/jobs/{id}/artifacts/{name}`
- `POST /api/v1/jobs/{id}/printer/upload`
- `POST /api/v1/jobs/{id}/printer/print` with `{ "confirm": true }`

## Development

```sh
uv sync --extra dev
uv run ruff check .
uv run pytest
cd frontend
npm ci
npm run lint
npm run build
```

Run the API and worker in separate terminals with matching environment variables:

```sh
AXIOM_TOKEN=development AXIOM_PROFILE_ROOT="$HOME/.var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer" uv run axiom-slice-api
AXIOM_TOKEN=development AXIOM_PROFILE_ROOT="$HOME/.var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer" AXIOM_ORCA_BIN=/path/to/orca-slicer uv run axiom-slice-worker
```

## Fedora deployment

The deployed service uses two rootless Podman images and user Quadlets. The API has LAN/Tailscale and printer access. The worker has `Network=none`; both containers mount Orca profiles read-only.

```sh
podman build --target api -t localhost/axiom-slice-api:1.0.0 .
podman build --target worker -t localhost/axiom-slice-worker:1.0.0 .
install -d -m 700 ~/.config/axiom-slice ~/.config/containers/systemd ~/.local/share/axiom-slice
install -m 600 deploy/service.env.example ~/.config/axiom-slice/service.env
install -m 644 deploy/*.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user enable --now axiom-slice-api.service axiom-slice-worker.service
```

Replace the example token before starting the services. Browse to `http://fedora.local:5000` on LAN or the Fedora Tailscale address when remote.

## Client

`axiom-slice` reads `AXIOM_SLICE_URL` and `AXIOM_SLICE_TOKEN` from the environment or `~/.config/axiom-slice/client.env`. It supports profiles, submit, jobs, status, wait, cancel, download, and upload. It deliberately does not expose print start.

Select one of the plate values returned by `axiom-slice profiles`, for example:

```sh
axiom-slice submit model.stl --bed-type textured-pei
```

Old terminal jobs are retained until a local administrator deliberately runs `axiom-slice-prune --older-than-days 30 --apply`; without `--apply`, it is a dry run.
