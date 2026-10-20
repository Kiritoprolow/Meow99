"""Entry point cho GitHub Actions:  python -m video_engine.run --job-id job_... --attempt 1"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

from app.config import JOB_ID_RE, Settings
from app.job_store import make_store
from video_engine.pipeline import Cancelled, run_job
from video_engine.storage.outputs import HubStorage, LocalStorage


def _on_signal(signum, _frame):  # GitHub gửi SIGINT/SIGTERM khi hủy run
    raise Cancelled(f"signal {signum}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--attempt", type=int, default=1)
    a = ap.parse_args(argv)
    if not JOB_ID_RE.match(a.job_id) or not 1 <= a.attempt <= 5:
        print("job_id hoặc attempt không hợp lệ", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    s = Settings.from_env()
    store = make_store(s)
    storage = LocalStorage(s.local_store_dir) if s.store_backend == "local" else HubStorage(s.hf_token, s.hf_output_repo or s.state_repo)
    rec = run_job(a.job_id, a.attempt, store, storage)
    print(f"job={a.job_id} status={rec.get('status')} stage={rec.get('stage')}")
    if rec.get("error"):
        print(f"error={rec['error']}", file=sys.stderr)
    return 0 if rec.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
