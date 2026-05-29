import argparse
import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.job_types import SAFE_JOB_TYPES
from automation.plan_parser import build_tasks_from_plan
from automation.storage import Storage


def read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class ControllerHandler(BaseHTTPRequestHandler):
    storage: Storage
    api_token: str

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _authorized(self) -> bool:
        token = self.headers.get("X-Automation-Token", "")
        if token == self.api_token:
            return True
        query = parse_qs(urlparse(self.path).query)
        return query.get("token", [""])[0] == self.api_token

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        write_json(self, 401, {"ok": False, "error": "unauthorized"})
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            write_json(self, 200, {"ok": True, "service": "central_controller"})
            return
        if not self._require_auth():
            return
        if parsed.path.startswith("/jobs/"):
            try:
                job_id = int(parsed.path.rsplit("/", 1)[1])
            except ValueError:
                write_json(self, 400, {"ok": False, "error": "invalid job id"})
                return
            job = self.storage.get_job(job_id)
            if job is None:
                write_json(self, 404, {"ok": False, "error": "job not found"})
                return
            write_json(self, 200, {"ok": True, "job": job})
            return
        if parsed.path == "/jobs":
            query = parse_qs(parsed.query)
            jobs = self.storage.list_jobs(
                status=query.get("status", [None])[0],
                job_type=query.get("job_type", [None])[0],
                node_id=query.get("node_id", [None])[0],
                limit=int(query.get("limit", ["50"])[0]),
            )
            write_json(self, 200, {"ok": True, "jobs": jobs, "count": len(jobs)})
            return
        if parsed.path == "/job-runs":
            query = parse_qs(parsed.query)
            raw_job_id = query.get("job_id", [None])[0]
            runs = self.storage.list_job_runs(
                job_id=int(raw_job_id) if raw_job_id else None,
                limit=int(query.get("limit", ["100"])[0]),
            )
            write_json(self, 200, {"ok": True, "runs": runs, "count": len(runs)})
            return
        if parsed.path == "/worker/next":
            query = parse_qs(parsed.query)
            node_id = query.get("node_id", [""])[0]
            if not node_id:
                write_json(self, 400, {"ok": False, "error": "missing node_id"})
                return
            job = self.storage.lease_next_job(node_id)
            write_json(self, 200, {"ok": True, "job": job})
            return
        if parsed.path == "/accounts":
            query = parse_qs(parsed.query)
            accounts = self.storage.list_accounts(
                group_id=query.get("group_id", [None])[0],
                node_id=query.get("node_id", [None])[0],
                limit=int(query.get("limit", ["500"])[0]),
            )
            write_json(self, 200, {"ok": True, "accounts": accounts, "count": len(accounts)})
            return
        if parsed.path == "/groups":
            query = parse_qs(parsed.query)
            groups = self.storage.list_groups(limit=int(query.get("limit", ["100"])[0]))
            write_json(self, 200, {"ok": True, "groups": groups, "count": len(groups)})
            return
        if parsed.path == "/workers":
            query = parse_qs(parsed.query)
            workers = self.storage.list_workers(limit=int(query.get("limit", ["100"])[0]))
            write_json(self, 200, {"ok": True, "workers": workers, "count": len(workers)})
            return
        if parsed.path == "/plans":
            query = parse_qs(parsed.query)
            plans = self.storage.list_plans(
                account_id=query.get("account_id", [None])[0],
                group_id=query.get("group_id", [None])[0],
                status=query.get("status", [None])[0],
                limit=int(query.get("limit", ["100"])[0]),
            )
            write_json(self, 200, {"ok": True, "plans": plans, "count": len(plans)})
            return
        if parsed.path == "/schedule":
            query = parse_qs(parsed.query)
            tasks = self.storage.list_scheduled_tasks(
                status=query.get("status", [None])[0],
                group_id=query.get("group_id", [None])[0],
                account_id=query.get("account_id", [None])[0],
                limit=int(query.get("limit", ["100"])[0]),
            )
            write_json(self, 200, {"ok": True, "tasks": tasks, "count": len(tasks)})
            return
        if parsed.path == "/score-prompt":
            write_json(self, 200, {"ok": True, "prompt": self.storage.get_setting("score_prompt", "")})
            return
        if parsed.path == "/score-plans":
            query = parse_qs(parsed.query)
            plans = [
                item
                for item in self.storage.list_plans(
                    account_id=query.get("account_id", [None])[0],
                    group_id=query.get("group_id", [None])[0],
                    status=query.get("status", [None])[0],
                    limit=int(query.get("limit", ["100"])[0]),
                    current_only=query.get("current_only", ["1"])[0] in {"1", "true", "yes"},
                )
                if item.get("plan_type") == "score_plan" or (item.get("parsed_plan") or {}).get("type") == "score_plan"
            ]
            write_json(self, 200, {"ok": True, "plans": plans, "count": len(plans)})
            return
        if parsed.path.startswith("/score-plans/"):
            try:
                plan_id = int(parsed.path.strip("/").split("/")[1])
            except (IndexError, ValueError):
                write_json(self, 400, {"ok": False, "error": "invalid plan id"})
                return
            plan = self.storage.get_plan(plan_id)
            if not plan:
                write_json(self, 404, {"ok": False, "error": "plan not found"})
                return
            tasks = self.storage.list_scheduled_tasks(plan_id=plan_id, limit=500)
            write_json(self, 200, {"ok": True, "plan": plan, "tasks": tasks})
            return
        write_json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._require_auth():
            return
        try:
            payload = read_json(self)
        except json.JSONDecodeError as exc:
            write_json(self, 400, {"ok": False, "error": f"invalid json: {exc}"})
            return
        route = self._route(parsed.path)
        if route[0] == "jobs" and route[1] == "":
            job_type = payload.get("job_type", "")
            if job_type not in SAFE_JOB_TYPES:
                write_json(self, 400, {"ok": False, "error": f"unsupported job_type: {job_type}"})
                return
            job_id = self.storage.create_job(
                job_type=job_type,
                payload=payload.get("payload", {}),
                target_node_id=payload.get("target_node_id"),
            )
            write_json(self, 201, {"ok": True, "job_id": job_id})
            return
        if route[0] == "jobs" and route[1].isdigit() and route[2] in {"complete", "fail"}:
            job_id = int(route[1])
            node_id = payload.get("node_id", "")
            if not node_id:
                write_json(self, 400, {"ok": False, "error": "missing node_id"})
                return
            if route[2] == "complete":
                self.storage.complete_job(job_id, node_id, payload.get("result", {}))
            else:
                self.storage.fail_job(job_id, node_id, payload.get("error", "unknown error"))
            write_json(self, 200, {"ok": True})
            return
        if route[0] == "jobs" and route[1].isdigit() and route[2] == "cancel":
            try:
                result = self.storage.cancel_job(int(route[1]), payload.get("reason", "cancelled_by_user"))
            except Exception as exc:
                write_json(self, 400, {"ok": False, "error": str(exc)})
                return
            write_json(self, 200, {"ok": True, **result})
            return
        if parsed.path == "/worker/heartbeat":
            self.storage.heartbeat_worker(
                node_id=payload.get("node_id", ""),
                label=payload.get("label", ""),
                status=payload.get("status", "online"),
                meta=payload.get("meta", {}),
            )
            write_json(self, 200, {"ok": True})
            return
        if parsed.path.startswith("/jobs/") and parsed.path.endswith("/log"):
            try:
                job_id = int(parsed.path.strip("/").split("/")[1])
            except (IndexError, ValueError):
                write_json(self, 400, {"ok": False, "error": "invalid job id"})
                return
            node_id = payload.get("node_id", "")
            message = payload.get("message", "")
            status = payload.get("status", "log")
            if not node_id or not message:
                write_json(self, 400, {"ok": False, "error": "missing node_id or message"})
                return
            self.storage.add_job_run(job_id, node_id, status, message)
            write_json(self, 200, {"ok": True})
            return
        if parsed.path == "/accounts/sync":
            node_id = payload.get("node_id", "")
            if not node_id:
                write_json(self, 400, {"ok": False, "error": "missing node_id"})
                return
            count = self.storage.upsert_accounts(node_id, payload.get("accounts", []))
            write_json(self, 200, {"ok": True, "count": count})
            return
        if parsed.path == "/groups/alias":
            alias = payload.get("alias", "")
            group_id = payload.get("group_id", "")
            if not alias or not group_id:
                write_json(self, 400, {"ok": False, "error": "missing alias or group_id"})
                return
            self.storage.set_group_alias(alias, group_id)
            write_json(self, 200, {"ok": True})
            return
        if parsed.path == "/score-prompt":
            prompt = payload.get("prompt", "")
            if not str(prompt).strip():
                write_json(self, 400, {"ok": False, "error": "missing prompt"})
                return
            self.storage.set_setting("score_prompt", str(prompt))
            write_json(self, 200, {"ok": True})
            return
        if parsed.path == "/score-plans/grok/batch":
            group_id = payload.get("group_id", "")
            period = payload.get("period", "weekly")
            if not group_id:
                write_json(self, 400, {"ok": False, "error": "missing group_id"})
                return
            prompt = self.storage.get_setting("score_prompt", "").strip()
            if not prompt:
                write_json(self, 400, {"ok": False, "error": "missing central score prompt"})
                return
            created = self.storage.create_score_plan_jobs(
                group_id=group_id,
                period=period,
                prompt_text=prompt,
                target_node_id=payload.get("target_node_id"),
                limit=int(payload.get("limit", 500)),
            )
            job_ids = created.get("job_ids", [])
            write_json(self, 201, {"ok": True, "job_ids": job_ids, "count": len(job_ids), **created})
            return
        if parsed.path == "/plans/grok/batch":
            group_id = payload.get("group_id", "")
            period = payload.get("period", "weekly")
            if not group_id:
                write_json(self, 400, {"ok": False, "error": "missing group_id"})
                return
            prompt = self.storage.get_setting("score_prompt", "").strip()
            if not prompt:
                write_json(self, 400, {"ok": False, "error": "missing central score prompt"})
                return
            created = self.storage.create_score_plan_jobs(
                group_id=group_id,
                period=period,
                prompt_text=prompt,
                target_node_id=payload.get("target_node_id"),
                limit=int(payload.get("limit", 500)),
            )
            job_ids = created.get("job_ids", [])
            write_json(
                self,
                201,
                {
                    "ok": True,
                    "compat_from": "/plans/grok/batch",
                    "message": "legacy Grok plan endpoint now creates score_grok_plan jobs with central score prompt",
                    "job_ids": job_ids,
                    "count": len(job_ids),
                    **created,
                },
            )
            return
        if parsed.path == "/modes/run":
            group_id = payload.get("group_id", "")
            mode = int(payload.get("mode", 0) or 0)
            if not group_id or mode not in {1, 2, 3}:
                write_json(self, 400, {"ok": False, "error": "missing group_id or invalid mode"})
                return
            job_ids = self.storage.create_legacy_mode_jobs(
                group_id=group_id,
                mode=mode,
                payload=payload,
                target_node_id=payload.get("target_node_id"),
                limit=int(payload.get("limit", 500)),
            )
            write_json(self, 201, {"ok": True, "job_ids": job_ids, "count": len(job_ids)})
            return
        if parsed.path.startswith("/plans/") and parsed.path.endswith("/approve"):
            try:
                plan_id = int(parsed.path.strip("/").split("/")[1])
            except (IndexError, ValueError):
                write_json(self, 400, {"ok": False, "error": "invalid plan id"})
                return
            plan = self.storage.get_plan(plan_id)
            if plan is None:
                write_json(self, 404, {"ok": False, "error": "plan not found"})
                return
            tasks = payload.get("tasks")
            if tasks is None:
                tasks = build_tasks_from_plan(plan, max_days=int(payload.get("max_days", 7)))
            if payload.get("dispatch_now"):
                now = int(__import__("time").time())
                for task in tasks:
                    task["run_at"] = now
            count = self.storage.approve_plan(plan_id, tasks)
            write_json(self, 200, {"ok": True, "scheduled_count": count})
            return
        if parsed.path == "/plans/auto-schedule":
            result = self.storage.auto_schedule_plans(
                group_id=payload.get("group_id"),
                status=payload.get("status"),
                max_days=int(payload.get("max_days", 31)),
                limit=int(payload.get("limit", 100)),
            )
            write_json(self, 200, {"ok": True, **result})
            return
        if parsed.path.startswith("/score-plans/") and parsed.path.endswith("/schedule"):
            try:
                plan_id = int(parsed.path.strip("/").split("/")[1])
            except (IndexError, ValueError):
                write_json(self, 400, {"ok": False, "error": "invalid plan id"})
                return
            count = self.storage.auto_schedule_plan(plan_id, max_days=int(payload.get("max_days", 31)))
            write_json(self, 200, {"ok": True, "scheduled_count": count})
            return
        if parsed.path.startswith("/score-plans/"):
            parts = parsed.path.strip("/").split("/")
            try:
                plan_id = int(parts[1])
            except (IndexError, ValueError):
                write_json(self, 400, {"ok": False, "error": "invalid plan id"})
                return
            action = parts[2] if len(parts) >= 3 else ""
            if action not in {"pause", "resume", "delete", "cancel-all"}:
                write_json(self, 404, {"ok": False, "error": "not found"})
                return
            try:
                result = self.storage.set_score_plan_status(
                    plan_id,
                    action,
                    late_grace_seconds=int(payload.get("late_grace_seconds", 3600)),
                )
            except Exception as exc:
                write_json(self, 400, {"ok": False, "error": str(exc)})
                return
            write_json(self, 200, {"ok": True, **result})
            return
        if parsed.path.startswith("/scheduled-tasks/"):
            parts = parsed.path.strip("/").split("/")
            try:
                task_id = int(parts[1])
            except (IndexError, ValueError):
                write_json(self, 400, {"ok": False, "error": "invalid task id"})
                return
            action = parts[2] if len(parts) >= 3 else "update"
            try:
                if action == "pause":
                    self.storage.set_scheduled_task_status(task_id, "paused")
                elif action == "resume":
                    self.storage.set_scheduled_task_status(task_id, "scheduled")
                elif action == "cancel":
                    self.storage.set_scheduled_task_status(task_id, "cancelled_by_user")
                elif action == "update":
                    self.storage.update_scheduled_task(task_id, payload)
                else:
                    write_json(self, 404, {"ok": False, "error": "not found"})
                    return
            except Exception as exc:
                write_json(self, 400, {"ok": False, "error": str(exc)})
                return
            write_json(self, 200, {"ok": True})
            return
        if parsed.path == "/scheduler/dispatch":
            dispatched = self.storage.dispatch_due_tasks(
                limit=int(payload.get("limit", 100)),
                late_grace_seconds=int(payload.get("late_grace_seconds", 3600)),
            )
            write_json(self, 200, {"ok": True, "dispatched": dispatched, "count": len(dispatched)})
            return
        write_json(self, 404, {"ok": False, "error": "not found"})

    @staticmethod
    def _route(path: str) -> Tuple[str, ...]:
        parts = [part for part in path.strip("/").split("/") if part]
        return tuple(parts + ["", ""])[:3]


def main() -> None:
    parser = argparse.ArgumentParser(description="Central automation controller")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--db", default="automation/data/controller.db")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    storage = Storage(args.db)
    storage.init_db()
    ControllerHandler.storage = storage
    ControllerHandler.api_token = args.token

    server = ThreadingHTTPServer((args.host, args.port), ControllerHandler)
    print(f"central controller listening on http://{args.host}:{args.port}")
    print(f"central token fingerprint: {hashlib.sha256(args.token.encode('utf-8')).hexdigest()[:12]}")
    server.serve_forever()


if __name__ == "__main__":
    main()
