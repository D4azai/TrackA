"""
Tests for worker/refresh_worker.py

Tests the public-facing behaviour without touching a real database or Redis:
  - run_once() returns the correct summary shape
  - run_forever() stops when shutdown is requested
  - main() --once mode exits 0 on success, 1 on failures
  - main() registers signal handlers
"""

import os
import signal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")

# ── Stubs ──────────────────────────────────────────────────────────────────────

class _FakeRefreshService:
    """Minimal stand-in for RecommendationRefreshService."""

    def __init__(self, processed=1, succeeded=1, failed=0):
        self._summary = SimpleNamespace(
            processed=processed,
            succeeded=succeeded,
            failed=failed,
        )
        self.calls: list[int] = []

    def run_pending_jobs(self, limit: int = 10):
        self.calls.append(limit)
        return self._summary


class _FakeDB:
    def close(self):
        pass


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def patch_worker_deps(monkeypatch):
    """
    Patch SessionLocal and RecommendationRefreshService inside the worker module
    so no real DB or Redis is needed.
    """
    fake_svc = _FakeRefreshService()
    fake_db = _FakeDB()

    monkeypatch.setattr("worker.refresh_worker.SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        "worker.refresh_worker._build_refresh_service",
        lambda db: fake_svc,
    )
    return fake_svc


# ── Tests: run_once ────────────────────────────────────────────────────────────

def test_run_once_returns_summary_dict(patch_worker_deps):
    from worker.refresh_worker import run_once

    result = run_once(batch_size=5)

    assert result == {"processed": 1, "succeeded": 1, "failed": 0}
    assert patch_worker_deps.calls == [5]


def test_run_once_propagates_service_failure(monkeypatch):
    """run_once should surface exceptions so callers can handle them."""
    from worker import refresh_worker

    def _boom():
        raise RuntimeError("DB down")

    monkeypatch.setattr("worker.refresh_worker.SessionLocal", _boom)

    with pytest.raises(RuntimeError, match="DB down"):
        refresh_worker.run_once()


# ── Tests: run_forever ─────────────────────────────────────────────────────────

def test_run_forever_stops_on_shutdown_flag(patch_worker_deps, monkeypatch):
    """
    Simulate setting _shutdown_requested=True after the first cycle.
    run_forever() should exit without sleeping the full poll_interval.
    """
    import worker.refresh_worker as worker_mod

    cycle_counter = {"n": 0}
    original_run_once = worker_mod.run_once

    def _counting_run_once(batch_size=10):
        result = original_run_once(batch_size)
        cycle_counter["n"] += 1
        # Signal shutdown after first cycle
        worker_mod._shutdown_requested = True
        return result

    monkeypatch.setattr(worker_mod, "run_once", _counting_run_once)
    monkeypatch.setattr(worker_mod, "_shutdown_requested", False)

    worker_mod.run_forever(poll_interval=0, batch_size=5)

    assert cycle_counter["n"] == 1, "Should have run exactly one cycle before stopping"

    # Reset global flag
    worker_mod._shutdown_requested = False


def test_run_forever_continues_after_exception(patch_worker_deps, monkeypatch):
    """A bad cycle should be logged and skipped, not crash the worker."""
    import worker.refresh_worker as worker_mod

    call_count = {"n": 0}

    def _flaky_run_once(batch_size=10):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient error")
        # Trigger shutdown after the second successful cycle
        worker_mod._shutdown_requested = True
        return {"processed": 1, "succeeded": 1, "failed": 0}

    monkeypatch.setattr(worker_mod, "run_once", _flaky_run_once)
    monkeypatch.setattr(worker_mod, "_shutdown_requested", False)

    # Should not raise despite the first-cycle error
    worker_mod.run_forever(poll_interval=0, batch_size=5)

    assert call_count["n"] == 2
    worker_mod._shutdown_requested = False


# ── Tests: main() CLI ──────────────────────────────────────────────────────────

def test_main_once_exits_zero_on_success(patch_worker_deps):
    from worker.refresh_worker import main

    exit_code = main(["--once", "--batch-size", "3"])
    assert exit_code == 0


def test_main_once_exits_one_on_failures(monkeypatch):
    import worker.refresh_worker as worker_mod

    failing_svc = _FakeRefreshService(processed=2, succeeded=1, failed=1)
    monkeypatch.setattr("worker.refresh_worker.SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr("worker.refresh_worker._build_refresh_service", lambda db: failing_svc)

    exit_code = worker_mod.main(["--once"])
    assert exit_code == 1


def test_main_registers_signal_handlers(patch_worker_deps, monkeypatch):
    """Verify that SIGTERM and SIGINT handlers are registered."""
    registered: dict[int, object] = {}
    original_signal = signal.signal

    def _capture_signal(signum, handler):
        registered[signum] = handler
        # Don't actually install (avoids test-runner side-effects)

    monkeypatch.setattr(signal, "signal", _capture_signal)

    # Use --once so main() doesn't block
    from worker.refresh_worker import main
    main(["--once"])

    assert signal.SIGTERM in registered
    assert signal.SIGINT in registered
