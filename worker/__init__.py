"""
Recommendation refresh worker package.

Two runnable entry points:
  python -m worker.refresh_worker   — polling worker (always-on)
  python -m worker.scheduler        — APScheduler (timed enqueue + drain)
"""
