"""The store-swap race: a retired connection must never close under a thread
that is still using it.

`get_store()` is a path-keyed singleton. A swap (a different path, or
`reset_stores()`) used to `close()` the previous store synchronously, while
other threads legitimately held the object they had been handed moments
earlier. The deterministic failure, reproduced in a five-line script before
the fix: grab the store, let another thread swap, call any method —
`ProgrammingError: Cannot operate on a closed database`, which surfaced to
callers as an unexplained 500.

The contract now:

1. a swap RETIRES the old store with a grace window (in-flight calls finish);
2. the grace expiry reaps the retired connection for real (no handle leak);
3. `reset_stores()` still force-closes immediately — the test suite needs
   file handles freed between cases.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from src.desk.store import DeskStore, get_store, reset_stores


@pytest.fixture()
def two_dbs(tmp_path, monkeypatch):
    db1 = str(tmp_path / "one.db")
    db2 = str(tmp_path / "two.db")
    for path in (db1, db2):
        DeskStore(path).close()
    monkeypatch.setenv("TRADEFLOW_DESK_DB", db1)
    reset_stores()
    yield db1, db2
    reset_stores()


class TestSwapRace:
    def test_an_in_flight_call_survives_a_swap(self, two_dbs):
        """The deterministic five-liner that failed before the fix."""
        db1, db2 = two_dbs
        held = []
        errors = []

        def grab_and_use():
            store = get_store()
            held.append(store)
            time.sleep(1.0)  # the swap + grace expiry happen during this sleep
            try:
                store.list_deals()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

        def swap():
            time.sleep(0.2)
            get_store(db2)  # retires db1's store

        t1 = threading.Thread(target=grab_and_use)
        t2 = threading.Thread(target=swap)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert errors == [], errors

    def test_many_threads_hammering_through_a_swap_never_see_closed(self, two_dbs):
        db1, db2 = two_dbs
        errors = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    get_store(db1).list_deals()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")
                    return

        def flipper():
            i = 0
            while not stop.is_set():
                get_store(db1 if i % 2 == 0 else db2)
                i += 1

        readers = [threading.Thread(target=reader) for _ in range(4)]
        flip = threading.Thread(target=flipper)
        for t in readers:
            t.start()
        flip.start()
        time.sleep(3)
        stop.set()
        for t in readers:
            t.join(timeout=5)
        flip.join(timeout=5)
        assert errors == [], errors

    def test_grace_expiry_reaps_the_retired_connection(self, two_dbs):
        """No handle leak: inside the grace window the retired store still
        serves in-flight calls (that is the fix); after the window, the NEXT
        get_store() call reaps it for real, so the fix does not trade the
        race for a leak."""
        db1, db2 = two_dbs
        old = get_store(db1)
        get_store(db2)  # retires old
        time.sleep(0.05)  # well inside the grace window: in-flight calls work
        old.list_deals()  # must NOT raise — a reader holding it is fine
        time.sleep(5.5)  # past the 5s grace window
        get_store(db2)  # any next call reaps the expired retired store
        time.sleep(0.1)
        with pytest.raises(sqlite3.ProgrammingError):
            old.list_deals()  # the connection was genuinely closed
