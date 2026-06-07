import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sys
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
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


def hash_password(password: str, iterations: int = 260000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


class ControllerHandler(BaseHTTPRequestHandler):
    storage: Storage
    api_token: str
    admin_username: str
    admin_password_hash: str
    admin_session_secret: str
    admin_cookie_name = "xbot_admin_session"
    admin_session_seconds = 8 * 3600
    web_root = Path(__file__).resolve().parent / "web_admin"

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

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
        return self.client_address[0] if self.client_address else ""

    @classmethod
    def _sign_session(cls, username: str, expires: int) -> str:
        payload = f"{username}|{expires}"
        sig = hmac.new(cls.admin_session_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}|{sig}".encode("utf-8")).decode("ascii")

    @classmethod
    def _verify_session(cls, token: str) -> Optional[str]:
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            username, expires_text, sig = raw.rsplit("|", 2)
            expires = int(expires_text)
        except Exception:
            return None
        if expires < int(time.time()):
            return None
        payload = f"{username}|{expires}"
        expected = hmac.new(cls.admin_session_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        return username

    def _admin_user(self) -> Optional[str]:
        raw_cookie = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw_cookie)
        except cookies.CookieError:
            return None
        morsel = jar.get(self.admin_cookie_name)
        if not morsel:
            return None
        return self._verify_session(morsel.value)

    def _require_admin(self) -> Optional[str]:
        username = self._admin_user()
        if username:
            return username
        write_json(self, 401, {"ok": False, "error": "admin login required"})
        return None

    def _set_admin_cookie(self, username: str) -> None:
        expires = int(time.time()) + self.admin_session_seconds
        token = self._sign_session(username, expires)
        self.send_header(
            "Set-Cookie",
            f"{self.admin_cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={self.admin_session_seconds}",
        )

    def _clear_admin_cookie(self) -> None:
        self.send_header(
            "Set-Cookie",
            f"{self.admin_cookie_name}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )

    def _write_admin_json(
        self,
        status: int,
        payload: Dict[str, Any],
        set_cookie_user: Optional[str] = None,
        clear_cookie: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if set_cookie_user:
            self._set_admin_cookie(set_cookie_user)
        if clear_cookie:
            self._clear_admin_cookie()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _audit(
        self,
        actor: str,
        action: str,
        target_type: str = "",
        target_id: str = "",
        detail: Optional[Dict[str, Any]] = None,
        ok: bool = True,
    ) -> None:
        try:
            self.storage.add_admin_audit(actor, action, target_type, target_id, detail or {}, ok=ok, ip=self._client_ip())
        except Exception:
            return

    def _serve_admin_static(self, parsed_path: str) -> None:
        rel = "index.html" if parsed_path in {"/admin", "/admin/"} else parsed_path.removeprefix("/admin/") or "index.html"
        candidate = (self.web_root / rel).resolve()
        root = self.web_root.resolve()
        if root not in candidate.parents and candidate != root:
            self.send_error(403)
            return
        if not candidate.exists() or candidate.is_dir():
            candidate = root / "index.html"
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            write_json(self, 200, {"ok": True, "service": "central_controller"})
            return
        if parsed.path == "/admin" or parsed.path.startswith("/admin/"):
            if parsed.path.startswith("/admin/api/"):
                self._handle_admin_get(parsed)
                return
            self._serve_admin_static(parsed.path)
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
                source=query.get("source", [None])[0],
                group_id=query.get("group_id", [None])[0],
                account_id=query.get("account_id", [None])[0],
                mode=query.get("mode", [None])[0],
                sort=query.get("sort", ["latest"])[0],
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
        if parsed.path == "/worker/config":
            query = parse_qs(parsed.query)
            node_id = query.get("node_id", [""])[0]
            if not node_id:
                write_json(self, 400, {"ok": False, "error": "missing node_id"})
                return
            config = self.storage.get_worker_config(node_id, mask_secrets=False)
            write_json(self, 200, {"ok": True, "config": config})
            return
        if parsed.path == "/accounts":
            query = parse_qs(parsed.query)
            accounts = self.storage.list_accounts(
                group_id=query.get("group_id", [None])[0],
                node_id=query.get("node_id", [None])[0],
                limit=int(query.get("limit", ["500"])[0]),
                include_inactive=query.get("include_inactive", ["0"])[0] in {"1", "true", "yes"},
            )
            write_json(self, 200, {"ok": True, "accounts": accounts, "count": len(accounts)})
            return
        if parsed.path == "/groups":
            query = parse_qs(parsed.query)
            groups = self.storage.list_groups(limit=int(query.get("limit", ["100"])[0]))
            write_json(self, 200, {"ok": True, "groups": groups, "count": len(groups)})
            return
        if parsed.path == "/worker-sync-groups":
            query = parse_qs(parsed.query)
            groups = self.storage.list_worker_sync_groups(node_id=query.get("node_id", [None])[0])
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
                node_id=query.get("node_id", [None])[0],
                mode=query.get("mode", [None])[0],
                sort=query.get("sort", ["latest"])[0],
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
        if parsed.path.startswith("/admin/api/"):
            self._handle_admin_post(parsed)
            return
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
            job_payload = payload.get("payload", {}) or {}
            preempted = {}
            if job_type == "legacy_mode_run" and int(job_payload.get("mode") or 0) == 2:
                preempted = self.storage.create_mode2_preemptions([job_id])
            write_json(self, 201, {"ok": True, "job_id": job_id, **preempted})
            return
        if route[0] == "jobs" and route[1].isdigit() and route[2] in {"complete", "fail", "preempt"}:
            job_id = int(route[1])
            node_id = payload.get("node_id", "")
            if not node_id:
                write_json(self, 400, {"ok": False, "error": "missing node_id"})
                return
            if route[2] == "complete":
                self.storage.complete_job(job_id, node_id, payload.get("result", {}))
            elif route[2] == "preempt":
                self.storage.preempt_job(job_id, node_id, payload.get("message", "preempted_by_mode2"))
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
            result = self.storage.upsert_accounts(
                node_id,
                payload.get("accounts", []),
                synced_group_ids=payload.get("synced_group_ids") or [],
            )
            if isinstance(result, dict):
                write_json(self, 200, {"ok": True, **result})
            else:
                write_json(self, 200, {"ok": True, "count": result})
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
        if parsed.path == "/groups/alias/delete":
            alias = payload.get("alias", "")
            if not alias:
                write_json(self, 400, {"ok": False, "error": "missing alias"})
                return
            result = self.storage.delete_group_alias(alias)
            write_json(self, 200, {"ok": True, **result})
            return
        if parsed.path == "/worker-sync-groups":
            node_id = payload.get("node_id", "")
            group_id = payload.get("group_id", "")
            try:
                result = self.storage.add_worker_sync_group(node_id, group_id)
            except Exception as exc:
                write_json(self, 400, {"ok": False, "error": str(exc)})
                return
            write_json(self, 200, {"ok": True, **result})
            return
        if parsed.path == "/worker-sync-groups/delete":
            node_id = payload.get("node_id", "")
            group_id = payload.get("group_id", "")
            try:
                result = self.storage.remove_worker_sync_group(node_id, group_id)
            except Exception as exc:
                write_json(self, 400, {"ok": False, "error": str(exc)})
                return
            write_json(self, 200, {"ok": True, **result})
            return
        if parsed.path.startswith("/accounts/") and parsed.path.endswith("/status"):
            profile_id = parsed.path.strip("/").split("/")[1]
            status = payload.get("status", "")
            try:
                result = self.storage.set_account_status(profile_id, status)
            except Exception as exc:
                write_json(self, 400, {"ok": False, "error": str(exc)})
                return
            write_json(self, 200, {"ok": True, **result})
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
            preempted = self.storage.create_mode2_preemptions(job_ids) if mode == 2 else {}
            write_json(self, 201, {"ok": True, "job_ids": job_ids, "count": len(job_ids), **preempted})
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

    def _handle_admin_get(self, parsed: Any) -> None:
        path = parsed.path
        if path == "/admin/api/me":
            user = self._admin_user()
            if not user:
                write_json(self, 401, {"ok": False, "error": "admin login required"})
                return
            write_json(self, 200, {"ok": True, "user": user})
            return
        user = self._require_admin()
        if not user:
            return
        query = parse_qs(parsed.query)
        try:
            if path == "/admin/api/dashboard":
                write_json(self, 200, {"ok": True, **self.storage.admin_dashboard()})
                return
            if path == "/admin/api/workers":
                workers = self.storage.list_workers(limit=int(query.get("limit", ["200"])[0]))
                write_json(self, 200, {"ok": True, "workers": workers, "count": len(workers)})
                return
            if path.startswith("/admin/api/workers/") and path.endswith("/config"):
                node_id = path.split("/")[4]
                config = self.storage.get_worker_config(node_id, mask_secrets=True)
                write_json(self, 200, {"ok": True, "config": config})
                return
            if path == "/admin/api/groups":
                groups = self.storage.list_groups(limit=int(query.get("limit", ["300"])[0]))
                write_json(self, 200, {"ok": True, "groups": groups, "count": len(groups)})
                return
            if path == "/admin/api/accounts":
                accounts = self.storage.list_accounts(
                    group_id=query.get("group_id", [None])[0],
                    node_id=query.get("node_id", [None])[0],
                    limit=int(query.get("limit", ["1000"])[0]),
                    include_inactive=query.get("include_inactive", ["1"])[0] in {"1", "true", "yes"},
                )
                write_json(self, 200, {"ok": True, "accounts": accounts, "count": len(accounts)})
                return
            if path.startswith("/admin/api/accounts/") and path.endswith("/timeline"):
                profile_id = path.split("/")[4]
                timeline = self.storage.account_timeline(profile_id, limit=int(query.get("limit", ["100"])[0]))
                write_json(self, 200, {"ok": True, "timeline": timeline, "count": len(timeline)})
                return
            if path == "/admin/api/jobs":
                jobs = self.storage.list_jobs(
                    status=query.get("status", [None])[0],
                    job_type=query.get("job_type", [None])[0],
                    node_id=query.get("node_id", [None])[0],
                    source=query.get("source", [None])[0],
                    group_id=query.get("group_id", [None])[0],
                    account_id=query.get("account_id", [None])[0],
                    mode=query.get("mode", [None])[0],
                    sort=query.get("sort", ["latest"])[0],
                    limit=int(query.get("limit", ["200"])[0]),
                )
                write_json(self, 200, {"ok": True, "jobs": jobs, "count": len(jobs)})
                return
            if path.startswith("/admin/api/jobs/") and path.endswith("/runs"):
                job_id = int(path.split("/")[4])
                runs = self.storage.list_job_runs(job_id=job_id, limit=int(query.get("limit", ["300"])[0]))
                write_json(self, 200, {"ok": True, "runs": runs, "count": len(runs)})
                return
            if path.startswith("/admin/api/jobs/"):
                job_id = int(path.rsplit("/", 1)[1])
                job = self.storage.get_job(job_id)
                if not job:
                    write_json(self, 404, {"ok": False, "error": "job not found"})
                    return
                runs = self.storage.list_job_runs(job_id=job_id, limit=300)
                write_json(self, 200, {"ok": True, "job": job, "runs": runs})
                return
            if path == "/admin/api/score-plans":
                plans = [
                    item
                    for item in self.storage.list_plans(
                        account_id=query.get("account_id", [None])[0],
                        group_id=query.get("group_id", [None])[0],
                        status=query.get("status", [None])[0],
                        limit=int(query.get("limit", ["200"])[0]),
                        current_only=query.get("current_only", ["1"])[0] in {"1", "true", "yes"},
                    )
                    if item.get("plan_type") == "score_plan" or (item.get("parsed_plan") or {}).get("type") == "score_plan"
                ]
                write_json(self, 200, {"ok": True, "plans": plans, "count": len(plans)})
                return
            if path.startswith("/admin/api/score-plans/"):
                plan_id = int(path.rsplit("/", 1)[1])
                plan = self.storage.get_plan(plan_id)
                if not plan:
                    write_json(self, 404, {"ok": False, "error": "plan not found"})
                    return
                tasks = self.storage.list_scheduled_tasks(plan_id=plan_id, limit=500)
                write_json(self, 200, {"ok": True, "plan": plan, "tasks": tasks})
                return
            if path == "/admin/api/schedule":
                tasks = self.storage.list_scheduled_tasks(
                    status=query.get("status", [None])[0],
                    group_id=query.get("group_id", [None])[0],
                    account_id=query.get("account_id", [None])[0],
                    node_id=query.get("node_id", [None])[0],
                    mode=query.get("mode", [None])[0],
                    sort=query.get("sort", ["latest"])[0],
                    limit=int(query.get("limit", ["500"])[0]),
                )
                write_json(self, 200, {"ok": True, "tasks": tasks, "count": len(tasks)})
                return
            if path == "/admin/api/score-prompt":
                write_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "prompt": self.storage.get_setting("score_prompt", self.storage.default_score_prompt()),
                        "default_prompt": self.storage.default_score_prompt(),
                    },
                )
                return
            if path == "/admin/api/audit":
                logs = self.storage.list_admin_audit(limit=int(query.get("limit", ["200"])[0]))
                write_json(self, 200, {"ok": True, "logs": logs, "count": len(logs)})
                return
            if path == "/admin/api/settings":
                default_worker = self.storage.get_worker_default_config(mask_secrets=True)
                write_json(
                    self,
                    200,
                    {
                        "ok": True,
                        "settings": {
                            "admin_user": self.admin_username,
                            "token_fingerprint": hashlib.sha256(self.api_token.encode("utf-8")).hexdigest()[:12],
                            "db_path": str(self.storage.db_path),
                            "web_root": str(self.web_root),
                            "worker_default_config": default_worker,
                        },
                    },
                )
                return
            if path == "/admin/api/score-fallback-config":
                write_json(self, 200, {"ok": True, "config": self.storage.get_score_fallback_config()})
                return
        except Exception as exc:
            write_json(self, 500, {"ok": False, "error": str(exc)})
            return
        write_json(self, 404, {"ok": False, "error": "not found"})

    def _handle_admin_post(self, parsed: Any) -> None:
        path = parsed.path
        try:
            payload = read_json(self)
        except json.JSONDecodeError as exc:
            write_json(self, 400, {"ok": False, "error": f"invalid json: {exc}"})
            return
        if path == "/admin/api/login":
            username = str(payload.get("username") or "")
            password = str(payload.get("password") or "")
            ok = username == self.admin_username and verify_password(password, self.admin_password_hash)
            if not ok:
                self._audit(username or "unknown", "login_failed", "admin", username, ok=False)
                self._write_admin_json(401, {"ok": False, "error": "用户名或密码错误"})
                return
            self._audit(username, "login", "admin", username)
            self._write_admin_json(200, {"ok": True, "user": username}, set_cookie_user=username)
            return
        user = self._require_admin()
        if not user:
            return
        if path == "/admin/api/logout":
            self._audit(user, "logout", "admin", user)
            self._write_admin_json(200, {"ok": True}, clear_cookie=True)
            return
        try:
            if path.startswith("/admin/api/jobs/") and path.endswith("/cancel"):
                job_id = int(path.split("/")[4])
                result = self.storage.cancel_job(job_id, payload.get("reason", "cancelled_by_admin"))
                self._audit(user, "cancel_job", "job", str(job_id), {"result": result})
                write_json(self, 200, {"ok": True, **result})
                return
            if path == "/admin/api/jobs/bulk":
                job_ids = [int(item) for item in payload.get("job_ids", [])]
                action = str(payload.get("action") or "")
                if action != "cancel":
                    write_json(self, 400, {"ok": False, "error": "invalid bulk action"})
                    return
                result = self.storage.bulk_cancel_jobs(job_ids, payload.get("reason", "cancelled_by_admin_bulk"))
                self._audit(user, "bulk_cancel_jobs", "job", ",".join(map(str, job_ids)), {"result": result})
                write_json(self, 200, {"ok": True, **result})
                return
            if path == "/admin/api/maintenance/cleanup-stale":
                result = self.storage.cleanup_stale_jobs(
                    grace_seconds=int(payload.get("grace_seconds", 3600)),
                    include_score_jobs=bool(payload.get("include_score_jobs", False)),
                    include_legacy_scheduled=True,
                )
                self._audit(user, "cleanup_stale_jobs", "maintenance", "stale_jobs", {"result": result})
                write_json(self, 200, {"ok": True, **result})
                return
            if path.startswith("/admin/api/workers/") and path.endswith("/config"):
                node_id = path.split("/")[4]
                config = self.storage.update_worker_config(node_id, payload)
                self._audit(user, "update_worker_config", "worker", node_id, {"keys": sorted(payload.keys())})
                write_json(self, 200, {"ok": True, "config": config})
                return
            if path.startswith("/admin/api/workers/") and path.endswith("/sync-groups"):
                node_id = path.split("/")[4]
                group_ids = payload.get("sync_group_ids", payload.get("group_ids", []))
                if isinstance(group_ids, str):
                    group_ids = [part.strip() for part in group_ids.replace("\n", ",").split(",") if part.strip()]
                result = self.storage.set_worker_sync_groups(node_id, list(group_ids or []))
                self._audit(user, "set_worker_sync_groups", "worker", node_id, {"result": result})
                write_json(self, 200, {"ok": True, **result})
                return
            if path == "/admin/api/worker-default-config":
                config = self.storage.update_worker_default_config(payload)
                self._audit(user, "update_worker_default_config", "setting", "worker_default_config", {"keys": sorted(payload.keys())})
                write_json(self, 200, {"ok": True, "config": config})
                return
            if path == "/admin/api/score-fallback-config":
                config = self.storage.update_score_fallback_config(payload)
                self._audit(user, "update_score_fallback_config", "setting", "score_fallback_config", {"keys": sorted(payload.keys())})
                write_json(self, 200, {"ok": True, "config": config})
                return
            if path.startswith("/admin/api/score-plans/"):
                parts = path.strip("/").split("/")
                plan_id = int(parts[3])
                action = parts[4] if len(parts) > 4 else ""
                if action == "schedule":
                    count = self.storage.auto_schedule_plan(plan_id, max_days=int(payload.get("max_days", 31)))
                    self._audit(user, "schedule_score_plan", "score_plan", str(plan_id), {"scheduled_count": count})
                    write_json(self, 200, {"ok": True, "scheduled_count": count})
                    return
                if action in {"pause", "resume", "delete", "cancel-all"}:
                    result = self.storage.set_score_plan_status(
                        plan_id,
                        action,
                        late_grace_seconds=int(payload.get("late_grace_seconds", 3600)),
                    )
                    self._audit(user, f"{action}_score_plan", "score_plan", str(plan_id), {"result": result})
                    write_json(self, 200, {"ok": True, **result})
                    return
            if path.startswith("/admin/api/scheduled-tasks/"):
                parts = path.strip("/").split("/")
                task_id = int(parts[3])
                action = parts[4] if len(parts) > 4 else ""
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
                self._audit(user, f"{action}_scheduled_task", "scheduled_task", str(task_id), {"payload": payload})
                write_json(self, 200, {"ok": True})
                return
            if path == "/admin/api/schedule/bulk":
                task_ids = [int(item) for item in payload.get("task_ids", [])]
                action = str(payload.get("action") or "")
                if action not in {"pause", "resume", "cancel"}:
                    write_json(self, 400, {"ok": False, "error": "invalid bulk action"})
                    return
                for task_id in task_ids:
                    status = {"pause": "paused", "resume": "scheduled", "cancel": "cancelled_by_user"}[action]
                    self.storage.set_scheduled_task_status(task_id, status)
                self._audit(user, f"bulk_{action}_scheduled_tasks", "scheduled_task", ",".join(map(str, task_ids)))
                write_json(self, 200, {"ok": True, "count": len(task_ids)})
                return
            if path == "/admin/api/groups/alias":
                alias = payload.get("alias", "")
                group_id = payload.get("group_id", "")
                if not alias or not group_id:
                    write_json(self, 400, {"ok": False, "error": "missing alias or group_id"})
                    return
                self.storage.set_group_alias(alias, group_id)
                self._audit(user, "set_group_alias", "group", group_id, {"alias": alias})
                write_json(self, 200, {"ok": True})
                return
            if path == "/admin/api/groups/alias/delete":
                alias = payload.get("alias", "")
                result = self.storage.delete_group_alias(alias)
                self._audit(user, "delete_group_alias", "group_alias", alias, {"result": result})
                write_json(self, 200, {"ok": True, **result})
                return
            if path == "/admin/api/worker-sync-groups":
                node_id = payload.get("node_id", "")
                group_id = payload.get("group_id", "")
                result = self.storage.add_worker_sync_group(node_id, group_id)
                self._audit(user, "add_worker_sync_group", "worker", node_id, {"group_id": group_id, "result": result})
                write_json(self, 200, {"ok": True, **result})
                return
            if path == "/admin/api/worker-sync-groups/delete":
                node_id = payload.get("node_id", "")
                group_id = payload.get("group_id", "")
                result = self.storage.remove_worker_sync_group(node_id, group_id)
                self._audit(user, "remove_worker_sync_group", "worker", node_id, {"group_id": group_id, "result": result})
                write_json(self, 200, {"ok": True, **result})
                return
            if path.startswith("/admin/api/accounts/") and path.endswith("/status"):
                profile_id = path.split("/")[4]
                status = payload.get("status", "")
                result = self.storage.set_account_status(profile_id, status)
                self._audit(user, "set_account_status", "account", profile_id, {"status": status, "result": result})
                write_json(self, 200, {"ok": True, **result})
                return
            if path == "/admin/api/score-prompt":
                prompt = str(payload.get("prompt") or "")
                if not prompt.strip():
                    write_json(self, 400, {"ok": False, "error": "missing prompt"})
                    return
                self.storage.set_setting("score_prompt", prompt)
                self._audit(user, "update_score_prompt", "setting", "score_prompt", {"length": len(prompt)})
                write_json(self, 200, {"ok": True})
                return
            if path == "/admin/api/score-prompt/reset":
                prompt = self.storage.default_score_prompt()
                self.storage.set_setting("score_prompt", prompt)
                self._audit(user, "reset_score_prompt", "setting", "score_prompt", {"length": len(prompt)})
                write_json(self, 200, {"ok": True, "prompt": prompt})
                return
        except Exception as exc:
            self._audit(user, "admin_action_failed", "", "", {"path": path, "error": str(exc)}, ok=False)
            write_json(self, 400, {"ok": False, "error": str(exc)})
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
    parser.add_argument("--admin-user", default=os.environ.get("XBOT_ADMIN_USER", "admin"))
    parser.add_argument("--admin-password", default=os.environ.get("XBOT_ADMIN_PASSWORD", "admin123456"))
    parser.add_argument("--admin-password-hash", default=os.environ.get("XBOT_ADMIN_PASSWORD_HASH", ""))
    parser.add_argument("--admin-session-secret", default=os.environ.get("XBOT_ADMIN_SESSION_SECRET", ""))
    args = parser.parse_args()

    storage = Storage(args.db)
    storage.init_db()
    ControllerHandler.storage = storage
    ControllerHandler.api_token = args.token
    ControllerHandler.admin_username = args.admin_user
    ControllerHandler.admin_password_hash = args.admin_password_hash or hash_password(args.admin_password)
    ControllerHandler.admin_session_secret = args.admin_session_secret or hashlib.sha256(
        f"{args.token}|xbot-admin-session".encode("utf-8")
    ).hexdigest()

    server = ThreadingHTTPServer((args.host, args.port), ControllerHandler)
    print(f"central controller listening on http://{args.host}:{args.port}")
    print(f"admin panel: http://{args.host}:{args.port}/admin")
    if not args.admin_password_hash and args.admin_password == "admin123456":
        print("WARNING: using default admin password admin123456; set XBOT_ADMIN_PASSWORD on public servers")
    print(f"central token fingerprint: {hashlib.sha256(args.token.encode('utf-8')).hexdigest()[:12]}")
    server.serve_forever()


if __name__ == "__main__":
    main()
