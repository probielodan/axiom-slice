from __future__ import annotations

import signal
import time

from .config import Settings
from .slicer import Cancelled, run_job
from .store import JobStore


def serve(settings: Settings | None = None) -> None:
    settings = settings or Settings.from_env()
    settings.prepare()
    store = JobStore(settings.database)
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        job = store.claim()
        if not job:
            time.sleep(settings.poll_seconds)
            continue
        job_id = job["id"]

        def log(level: str, message: str, current_job_id: str = job_id) -> None:
            if message:
                store.event(current_job_id, message, level)

        try:
            result = run_job(settings, store, job, log)
        except Cancelled as error:
            store.finish(job_id, "cancelled", error=str(error))
        except Exception as error:
            store.finish(job_id, "failed", error=str(error))
        else:
            store.finish(job_id, "succeeded", result=result)


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
