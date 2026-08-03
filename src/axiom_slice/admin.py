from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime, timedelta

from .config import Settings
from .store import JobStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune old terminal Axiom Slice jobs")
    parser.add_argument("--older-than-days", type=int, default=30)
    parser.add_argument("--apply", action="store_true", help="Actually delete; the default is a dry run")
    args = parser.parse_args()
    if args.older_than_days < 1:
        raise SystemExit("--older-than-days must be at least 1")
    settings = Settings.from_env()
    store = JobStore(settings.database)
    cutoff = datetime.now(UTC) - timedelta(days=args.older_than_days)
    candidates = [
        job
        for job in store.list(10000)
        if job["status"] in {"succeeded", "failed", "cancelled"} and datetime.fromisoformat(job["finished_at"]) < cutoff
    ]
    for job in candidates:
        print(f"{'DELETE' if args.apply else 'WOULD DELETE'} {job['id']} {job['finished_at']}")
    if args.apply and candidates:
        with store.connect() as connection:
            for job in candidates:
                shutil.rmtree(settings.jobs_dir / job["id"], ignore_errors=True)
                connection.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
    print(f"{len(candidates)} job(s) {'deleted' if args.apply else 'eligible'}")
