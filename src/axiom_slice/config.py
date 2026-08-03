from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    profile_root: Path
    static_dir: Path
    token: str
    orca_bin: Path
    orca_version: str
    orca_digest: str
    printer_url: str
    max_upload_bytes: int
    poll_seconds: float

    @property
    def database(self) -> Path:
        return self.data_dir / "axiom-slice.sqlite3"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def user_profiles(self) -> Path:
        return self.profile_root / "user" / "default"

    @property
    def system_profiles(self) -> Path:
        return self.profile_root / "system"

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.environ.get("AXIOM_DATA_DIR", "~/.local/share/axiom-slice")).expanduser()
        profile_root = Path(os.environ.get("AXIOM_PROFILE_ROOT", "~/.config/OrcaSlicer")).expanduser()
        static_dir = Path(os.environ.get("AXIOM_STATIC_DIR", "frontend/dist")).expanduser()
        return cls(
            data_dir=data_dir,
            profile_root=profile_root,
            static_dir=static_dir,
            token=os.environ.get("AXIOM_TOKEN", ""),
            orca_bin=Path(os.environ.get("AXIOM_ORCA_BIN", "/opt/OrcaSlicer/AppRun")),
            orca_version=os.environ.get("AXIOM_ORCA_VERSION", "2.4.2"),
            orca_digest=os.environ.get(
                "AXIOM_ORCA_DIGEST",
                "sha256:d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd",
            ),
            printer_url=os.environ.get("AXIOM_PRINTER_URL", "http://3dprinter.home.arpa").rstrip("/"),
            max_upload_bytes=int(os.environ.get("AXIOM_MAX_UPLOAD_BYTES", str(250 * 1024 * 1024))),
            poll_seconds=float(os.environ.get("AXIOM_POLL_SECONDS", "0.5")),
        )

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
