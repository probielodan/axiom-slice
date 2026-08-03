from pathlib import Path

from axiom_slice.store import JobStore


def test_queue_claim_cancel_and_finish(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "state.sqlite3")
    store.create("first", {"slice": {}})
    store.create("second", {"slice": {}})
    claimed = store.claim()
    assert claimed and claimed["id"] == "first"
    assert store.request_cancel("first")
    assert store.status("first") == "cancel_requested"
    store.finish("first", "cancelled", error="cancelled")
    assert store.claim()["id"] == "second"
    store.finish("second", "succeeded", {"artifacts": {}})
    assert store.get("second")["status"] == "succeeded"
    assert len(store.events("second")) >= 3
