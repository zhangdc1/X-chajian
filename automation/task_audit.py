from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def now_ts() -> int:
    return int(time.time())


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_name(value: Any, default: str = "job") -> str:
    text = str(value or default)
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text)


class TaskAudit:
    """Local worker-side audit files for human review."""

    def __init__(self, task_dir: str, log_dir: str, node_id: str):
        self.node_id = node_id
        self.task_dir = Path(task_dir)
        self.log_root = Path(log_dir)
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.task_dir / "task_audit.jsonl"
        self.current_path = self.task_dir / "current_task.json"

    def log_path_for(self, job: Dict[str, Any]) -> Path:
        payload = job.get("payload") or {}
        mode = payload.get("mode") or job.get("job_type") or "job"
        if str(mode).isdigit():
            mode_text = f"mode{mode}"
        else:
            mode_text = safe_name(mode)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        node_dir = self.log_root / safe_name(self.node_id, "local-node")
        node_dir.mkdir(parents=True, exist_ok=True)
        return node_dir / f"job_{job.get('id')}_{mode_text}_{stamp}.txt"

    def append_log(self, log_path: Optional[str | Path], status: str, message: str) -> None:
        if not log_path:
            return
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = f"[{now_text()}] [{status}] {message}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

    def start_job(self, job: Dict[str, Any], log_path: str | Path) -> None:
        payload = job.get("payload") or {}
        record = {
            "event": "start",
            "job_id": job.get("id"),
            "source": payload.get("source") or "central",
            "job_type": job.get("job_type"),
            "mode": payload.get("mode"),
            "group_id": payload.get("group_id"),
            "account_id": payload.get("account_id") or payload.get("profile_id") or "general",
            "target_node_id": job.get("target_node_id"),
            "node_id": self.node_id,
            "started_at": now_ts(),
            "started_at_text": now_text(),
            "status": "running",
            "error": "",
            "log_file": str(log_path),
        }
        self.append_audit(record)
        self.write_current(record)
        self.append_log(
            log_path,
            "start",
            (
                f"当前任务ID: {job.get('id')} | "
                f"任务开始: type={job.get('job_type')} mode={payload.get('mode')} "
                f"account/profile={record['account_id']}"
            ),
        )
        self.append_log(log_path, "start", f"任务开始：job={job.get('id')} type={job.get('job_type')} mode={payload.get('mode')}")

    def finish_job(self, job: Dict[str, Any], status: str, error: str = "", result: Optional[Dict[str, Any]] = None) -> None:
        payload = job.get("payload") or {}
        log_path = payload.get("_local_log_path")
        record = {
            "event": "finish",
            "job_id": job.get("id"),
            "source": payload.get("source") or "central",
            "job_type": job.get("job_type"),
            "mode": payload.get("mode"),
            "group_id": payload.get("group_id"),
            "account_id": payload.get("account_id") or payload.get("profile_id") or "general",
            "target_node_id": job.get("target_node_id"),
            "node_id": self.node_id,
            "finished_at": now_ts(),
            "finished_at_text": now_text(),
            "status": status,
            "error": error,
            "log_file": str(log_path or ""),
            "result": result or {},
        }
        self.append_audit(record)
        self.append_log(log_path, status, error or f"任务结束：{status}")
        self.clear_current(job.get("id"))

    def append_audit(self, record: Dict[str, Any]) -> None:
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_current(self, record: Dict[str, Any]) -> None:
        with open(self.current_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    def clear_current(self, job_id: Any) -> None:
        if not self.current_path.exists():
            return
        try:
            data = json.loads(self.current_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if str(data.get("job_id")) == str(job_id):
            with open(self.current_path, "w", encoding="utf-8") as f:
                json.dump({"status": "idle", "node_id": self.node_id, "updated_at": now_ts(), "updated_at_text": now_text()}, f, ensure_ascii=False, indent=2)
