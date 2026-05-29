from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path


def cancel_score_plan_backlog(db_path: str) -> None:
    path = Path(db_path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    ts = int(time.time())
    rows = conn.execute(
        """
        SELECT id, status FROM jobs
        WHERE job_type = 'legacy_mode_run'
          AND status IN ('queued', 'leased')
          AND json_extract(payload_json, '$.source') = 'grok_score_plan'
        """
    ).fetchall()
    queued = [int(row["id"]) for row in rows if row["status"] == "queued"]
    leased = [int(row["id"]) for row in rows if row["status"] == "leased"]
    with conn:
        if queued:
            placeholders = ",".join("?" for _ in queued)
            conn.execute(
                f"UPDATE jobs SET status = 'cancelled', error = 'maintenance_cancel_backlog', updated_at = ? WHERE id IN ({placeholders})",
                [ts, *queued],
            )
        if leased:
            placeholders = ",".join("?" for _ in leased)
            conn.execute(
                f"UPDATE jobs SET status = 'cancel_requested', error = 'maintenance_cancel_backlog', updated_at = ? WHERE id IN ({placeholders})",
                [ts, *leased],
            )
        for job_id in queued:
            conn.execute(
                "INSERT INTO job_runs (job_id, node_id, status, message, created_at) VALUES (?, '', 'cancelled', 'maintenance_cancel_backlog', ?)",
                (job_id, ts),
            )
        for job_id in leased:
            conn.execute(
                "INSERT INTO job_runs (job_id, node_id, status, message, created_at) VALUES (?, '', 'cancel_requested', 'maintenance_cancel_backlog', ?)",
                (job_id, ts),
            )
    print(f"cancelled queued jobs: {len(queued)}")
    print(f"requested running jobs to stop: {len(leased)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Automation maintenance utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    cancel = sub.add_parser("cancel-score-plan-backlog")
    cancel.add_argument("--db", default="automation/data/controller.db")
    args = parser.parse_args()
    if args.command == "cancel-score-plan-backlog":
        cancel_score_plan_backlog(args.db)


if __name__ == "__main__":
    main()
