import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import yaml
except ImportError:  # pragma: no cover - existing project already uses yaml
    yaml = None

from automation.job_types import (
    JOB_COMMENT_DRAFT,
    JOB_CONTENT_DRAFT,
    JOB_GENERATE_GROK_PLAN,
    JOB_LEGACY_MODE_RUN,
    JOB_MANUAL_REVIEW,
    JOB_PARSE_PLAN,
    JOB_SCORE_GROK_PLAN,
)
from automation.bit_browser import BitBrowserClient
from automation.grok_adapter import GrokBrowserAdapter
from automation.license_guard import LicenseGuard
from automation.score_plan_parser import parse_score_plan
from automation.task_audit import TaskAudit


class JobPreempted(RuntimeError):
    pass


def load_config(path: str) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required for automation_config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(path: str, config: Dict[str, Any]) -> None:
    if yaml is None:
        return
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def api_json(method: str, url: str, token: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = None
    headers = {"X-Automation-Token": token}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = request.Request(url, data=data, headers=headers, method=method)
    opener = request.build_opener(request.ProxyHandler({}))
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            with opener.open(req, timeout=20) as res:
                return json.loads(res.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


class LocalLock:
    def __init__(self, lock_dir: str):
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def acquire(self, name: str) -> Optional[Path]:
        safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
        path = self.lock_dir / f"{safe_name}.lock"
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            return path
        except FileExistsError:
            return None

    @staticmethod
    def release(path: Optional[Path]) -> None:
        if not path:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class Worker:
    def __init__(self, config: Dict[str, Any], config_path: str = "automation_config.yaml"):
        self.config = config
        self.config_path = config_path
        self.node_id = config.get("node_id", "local-node")
        self.label = config.get("label", self.node_id)
        self.central_api = config["central_api"].rstrip("/")
        self.token = config["central_token"]
        self.lock = LocalLock(config.get("lock_dir", "automation/local_locks"))
        self.bit = BitBrowserClient(config.get("bit_api_url", "http://127.0.0.1:54345"))
        self.grok = GrokBrowserAdapter(config.get("grok", {}))
        self.license_guard = LicenseGuard(
            config.get("card_number"),
            config.get("app_version", "1.0.0"),
        )
        self._last_forwarded_legacy_log = ""
        self._last_forwarded_legacy_ts = 0.0
        self._current_log_path: Optional[Path] = None
        self.audit = TaskAudit(
            config.get("task_dir", "automation/tasks"),
            config.get("log_dir", "automation/logs"),
            self.node_id,
        )
        self._license_failure_count = 0
        Path(config.get("log_dir", "automation/logs")).mkdir(parents=True, exist_ok=True)
        Path(config.get("draft_output_path", "automation/output/comment_drafts.jsonl")).parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.pending_reports_path = Path(config.get("task_dir", "automation/tasks")) / "pending_job_reports.jsonl"

    @staticmethod
    def app_root() -> Path:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            if exe_dir.parent.name.lower() == "runtime":
                return exe_dir.parent.parent
            if exe_dir.name.lower() == "runtime":
                return exe_dir.parent
            return exe_dir
        return Path(__file__).resolve().parents[1]

    def tool_command(self, exe_name: str, script_name: str) -> list[str]:
        script = Path(__file__).resolve().parent / script_name
        if getattr(sys, "frozen", False):
            exe_path = self.app_root() / "runtime" / Path(exe_name).stem / exe_name
            if exe_path.exists():
                return [str(exe_path)]
            raise RuntimeError(f"发布包缺少运行组件：{exe_path}")
        return [sys.executable, str(script)]

    def run_forever(self) -> None:
        if not self.config.get("worker_enabled", True):
            print("worker_enabled=false, exiting")
            return
        if self.config.get("require_license", False):
            result = self.license_guard.validate_once()
            if not result.ok:
                raise RuntimeError(f"license validation failed: {result.message}")
            print(f"license ok: {result.message}")

        poll = int(self.config.get("poll_interval_seconds", 5))
        last_heartbeat = 0.0
        last_license_heartbeat = time.time()
        last_profile_sync = 0.0
        while True:
            now = time.time()
            self.flush_pending_reports()
            if now - last_heartbeat >= 30:
                self.heartbeat()
                last_heartbeat = now
            if self.config.get("require_license", False) and self.config.get("license_heartbeat_enabled", False):
                interval = int(self.config.get("license_heartbeat_seconds", 60) or 60)
                if now - last_license_heartbeat >= interval:
                    self.check_license_heartbeat()
                    last_license_heartbeat = now
            sync_interval = int(self.config.get("sync_profiles_interval_seconds", 60))
            if self.config.get("sync_profiles_on_start", True) and now - last_profile_sync >= sync_interval:
                self.refresh_assigned_sync_groups()
                self.sync_profiles()
                last_profile_sync = now
            job = self.next_job()
            if job:
                try:
                    self.handle_job(job)
                except Exception as exc:
                    job_id = job.get("id", "?")
                    print(f"worker job loop recovered after job {job_id} failed unexpectedly: {exc}")
                    try:
                        self.log_job(int(job_id), "error", f"Worker 捕获到未处理异常，已保持在线：{exc}")
                        self.fail(int(job_id), str(exc))
                    except Exception as report_exc:
                        print(f"failed to report unexpected job error: {report_exc}")
            else:
                time.sleep(poll)

    def heartbeat(self) -> None:
        payload = {
            "node_id": self.node_id,
            "label": self.label,
            "status": "online",
            "meta": {
                "bit_api_url": self.config.get("bit_api_url"),
                "respect_manual_open_profiles": self.config.get("respect_manual_open_profiles", True),
                "enable_grok_browser": self.config.get("enable_grok_browser", False),
                "sync_group_ids": self.config.get("sync_group_ids") or [],
            },
        }
        try:
            api_json("POST", f"{self.central_api}/worker/heartbeat", self.token, payload)
        except Exception as exc:
            print(f"heartbeat failed: {exc}")

    def check_license_heartbeat(self) -> None:
        max_failures = int(self.config.get("license_max_failures", 3) or 3)
        result = self.license_guard.heartbeat()
        if result.ok:
            if self._license_failure_count:
                print(f"license heartbeat recovered: {result.message}")
            self._license_failure_count = 0
            return
        self._license_failure_count += 1
        print(f"license heartbeat failed {self._license_failure_count}/{max_failures}: {result.message}")
        if self._license_failure_count >= max_failures:
            raise RuntimeError("卡密心跳连续失败，Worker 已停止领取任务")

    def sync_profiles(self) -> None:
        group_ids = self.config.get("sync_group_ids") or []
        if not group_ids:
            return
        all_profiles = []
        for group_id in group_ids:
            try:
                profiles = self.bit.list_profiles(str(group_id))
                all_profiles.extend(profiles)
            except Exception as exc:
                print(f"sync group {group_id} failed: {exc}")
        if not all_profiles:
            return
        payload = {
            "node_id": self.node_id,
            "synced_group_ids": [str(item) for item in group_ids],
            "accounts": all_profiles,
        }
        try:
            response = api_json("POST", f"{self.central_api}/accounts/sync", self.token, payload)
            print(
                "synced profiles: "
                f"{response.get('count', 0)} active, "
                f"deactivated={response.get('deactivated', 0)}"
            )
        except Exception as exc:
            print(f"profile sync upload failed: {exc}")

    def refresh_assigned_sync_groups(self) -> None:
        try:
            response = api_json("GET", f"{self.central_api}/worker-sync-groups?node_id={self.node_id}", self.token)
        except Exception as exc:
            print(f"worker sync group refresh failed: {exc}")
            return
        assigned = [str(item.get("group_id") or "").strip() for item in response.get("groups", [])]
        assigned = [item for item in assigned if item]
        if not assigned:
            return
        existing = [str(item).strip() for item in (self.config.get("sync_group_ids") or []) if str(item).strip()]
        merged = list(dict.fromkeys([*existing, *assigned]))
        if merged == existing:
            return
        self.config["sync_group_ids"] = merged
        try:
            save_config(self.config_path, self.config)
            print(f"updated local sync_group_ids from central: {','.join(assigned)}")
        except Exception as exc:
            print(f"save sync_group_ids failed: {exc}")

    def next_job(self) -> Optional[Dict[str, Any]]:
        url = f"{self.central_api}/worker/next?node_id={self.node_id}"
        try:
            response = api_json("GET", url, self.token)
            return response.get("job")
        except Exception as exc:
            print(f"next job failed: {exc}")
            return None

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        try:
            response = api_json("GET", f"{self.central_api}/jobs/{job_id}", self.token)
            return response.get("job")
        except Exception as exc:
            print(f"job status check failed: {exc}")
            return None

    def is_job_cancelled(self, job_id: int) -> bool:
        job = self.get_job(job_id)
        return bool(job and job.get("status") in {"cancelled", "cancel_requested"})

    def job_cancel_reason(self, job_id: int) -> str:
        job = self.get_job(job_id)
        return str((job or {}).get("error") or "")

    def fetch_score_prompt(self) -> str:
        data = api_json("GET", f"{self.central_api}/score-prompt", self.token)
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            raise RuntimeError("中央评分提示词为空，请先在 GUI 的账号评分计划页保存提示词")
        return prompt

    def handle_job(self, job: Dict[str, Any]) -> None:
        payload = job.setdefault("payload", {})
        if job.get("status") in {"cancelled", "cancel_requested"} or self.is_job_cancelled(int(job["id"])):
            self.fail(job["id"], "任务已取消，未执行")
            return
        account_id = str(payload.get("account_id") or payload.get("profile_id") or "general")
        log_path = self.audit.log_path_for(job)
        payload["_local_log_path"] = str(log_path)
        self._current_log_path = log_path
        self.audit.start_job(job, log_path)
        mode = payload.get("mode") or "-"
        self.log_job(
            job["id"],
            "running",
            f"当前任务ID: {job['id']} | 类型: {job.get('job_type')} | 模式: {mode} | 账号/profile: {account_id}",
        )
        self.log_job(job["id"], "running", f"开始执行任务：{job.get('job_type')}，账号/profile={account_id}")
        self.log_job(job["id"], "log", f"本地日志文件：{log_path}")
        lock_path = self.lock.acquire(account_id)
        if lock_path is None:
            error = f"local lock busy for account/profile {account_id}"
            self.audit.finish_job(job, "failed", error=error)
            self._current_log_path = None
            self.fail(job["id"], error)
            return
        try:
            result = self.execute(job)
            result.setdefault("local_log_path", str(log_path))
            self.log_job(job["id"], "running", f"任务执行完成，准备回传结果：{result.get('status', 'ok')}")
            self.audit.finish_job(job, "completed", result=result)
            if not self.report_job_status("complete", job["id"], result=result):
                self.queue_pending_report("complete", job["id"], result=result)
                self.audit.append_log(
                    log_path,
                    "pending_report",
                    "任务本地已完成，但中央回传失败，已加入待补报队列；不会把任务改为失败。",
                )
        except Exception as exc:
            self.log_job(job["id"], "error", f"任务执行失败：{exc}")
            if isinstance(exc, JobPreempted):
                self.audit.finish_job(job, "preempted", error=str(exc))
                if not self.report_job_status("preempt", job["id"], error=str(exc)):
                    self.queue_pending_report("preempt", job["id"], error=str(exc))
                return
            self.audit.finish_job(job, "failed", error=str(exc))
            if not self.report_job_status("fail", job["id"], error=str(exc)):
                self.queue_pending_report("fail", job["id"], error=str(exc))
        finally:
            self.lock.release(lock_path)
            self._current_log_path = None

    def execute(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_type = job["job_type"]
        payload = job.get("payload", {})
        payload["_job_id"] = job["id"]
        if job_type == JOB_GENERATE_GROK_PLAN:
            return self.generate_grok_plan(payload)
        if job_type == JOB_SCORE_GROK_PLAN:
            return self.generate_score_plan(payload)
        if job_type == JOB_PARSE_PLAN:
            return self.parse_plan(payload)
        if job_type == JOB_COMMENT_DRAFT:
            return self.comment_draft(payload)
        if job_type == JOB_CONTENT_DRAFT:
            return self.content_draft(payload)
        if job_type == JOB_LEGACY_MODE_RUN:
            return self.legacy_mode_run(payload)
        if job_type == JOB_MANUAL_REVIEW:
            return {"status": "waiting_for_manual_review", "payload": payload}
        raise ValueError(f"unsupported job type: {job_type}")

    def generate_grok_plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        period = payload.get("period", "weekly")
        account_id = payload.get("account_id", "")
        profile_id = payload.get("profile_id") or account_id
        prompt = payload.get(
            "prompt",
            f"请为账号 {account_id} 生成 {period} 内容运营计划，输出每天的内容主题、发布建议和复盘指标。",
        )
        if self.config.get("enable_grok_browser", False):
            self.log_job(
                int(payload.get("_job_id", 0) or 0),
                "running",
                f"将打开比特浏览器 profile={profile_id} 并进入 X Grok",
            )
            opened = self.bit.open_profile(str(profile_id))
            debug_port = self._extract_debug_port(opened)
            if not debug_port:
                raise RuntimeError(f"Bit Browser did not return a debug port: {opened}")
            result = self.grok.ask_with_debug_port(debug_port, prompt)
            if not result.ok:
                raise RuntimeError(result.error)
            return {
                "status": "grok_raw_collected",
                "account_id": account_id,
                "profile_id": profile_id,
                "period": period,
                "prompt": prompt,
                "grok_raw_response": result.raw_response,
            }
        return {
            "status": "stub",
            "next_step": "connect Grok browser adapter",
            "account_id": account_id,
            "profile_id": profile_id,
            "period": period,
            "prompt": prompt,
        }

    def generate_score_plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        period = payload.get("period", "weekly")
        account_id = payload.get("account_id", "")
        profile_id = payload.get("profile_id") or account_id
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            prompt = self.fetch_score_prompt()
        self.log_job(
            int(payload.get("_job_id", 0) or 0),
            "running",
            "评分任务使用中央创建时写入的提示词快照；payload 为空时才回源读取中央提示词",
        )
        if self.config.get("enable_grok_browser", False):
            self.log_job(
                int(payload.get("_job_id", 0) or 0),
                "running",
                f"将打开比特浏览器 profile={profile_id} 并进入 X Grok 执行账号评分",
            )
            opened = self.bit.open_profile(str(profile_id))
            debug_port = self._extract_debug_port(opened)
            if not debug_port:
                raise RuntimeError(f"Bit Browser did not return a debug port: {opened}")
            result = self.grok.ask_with_debug_port(debug_port, prompt)
            if not result.ok:
                raise RuntimeError(result.error)
            parsed = parse_score_plan(result.raw_response, period=period, seed_extra=str(profile_id or account_id))
            return {
                "status": "score_plan_collected",
                "account_id": account_id,
                "profile_id": profile_id,
                "period": period,
                "prompt": prompt,
                "grok_raw_response": result.raw_response,
                "parsed_plan": parsed,
            }
        return {
            "status": "stub",
            "next_step": "enable_grok_browser=true",
            "account_id": account_id,
            "profile_id": profile_id,
            "period": period,
            "prompt": prompt,
            "parsed_plan": parse_score_plan("", period=period, seed_extra=str(profile_id or account_id)),
        }

    @staticmethod
    def _extract_debug_port(opened_profile: Dict[str, Any]) -> Optional[int]:
        candidates = [
            opened_profile.get("http"),
            opened_profile.get("debuggingPort"),
            opened_profile.get("debug_port"),
            opened_profile.get("port"),
        ]
        for item in candidates:
            if item is None:
                continue
            if isinstance(item, int):
                return item
            text = str(item)
            if ":" in text:
                text = text.rsplit(":", 1)[-1]
            try:
                return int(text)
            except ValueError:
                continue
        return None

    def parse_plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = payload.get("grok_raw_response", "")
        return {
            "status": "stub",
            "parsed_plan": {
                "source_length": len(raw),
                "requires_review": True,
                "tasks": [],
            },
        }

    def comment_draft(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        tweet_text = payload.get("tweet_text", "")
        account_id = payload.get("account_id", "")
        draft = payload.get("draft") or self.simple_comment_draft(tweet_text)
        record = {
            "created_at": int(time.time()),
            "account_id": account_id,
            "tweet_url": payload.get("tweet_url", ""),
            "tweet_text": tweet_text,
            "draft_text": draft,
            "status": "drafted",
        }
        out_path = Path(self.config.get("draft_output_path", "automation/output/comment_drafts.jsonl"))
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"status": "drafted", "draft": draft, "saved_to": str(out_path)}

    def content_draft(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        topic = payload.get("topic", "今日观察")
        return {
            "status": "drafted",
            "text": f"{topic}：这里先生成一条待审核内容草稿，后续可接入模型生成更自然的版本。",
        }

    def legacy_mode_run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mode = int(payload.get("mode") or 1)
        if mode not in {1, 2, 3}:
            raise ValueError(f"unsupported legacy mode: {mode}")
        job_id = int(payload.get("_job_id", 0) or 0)
        timeout_seconds = int(self.config.get("legacy_job_timeout_seconds", 7200))
        self.log_job(
            job_id,
            "running",
            f"准备执行原脚本模式{mode} | 当前任务ID: {job_id} | 超时上限: {timeout_seconds}秒",
        )
        self.log_job(job_id, "running", f"准备执行原脚本模式{mode}，参数将读取现有 config.yaml 并应用本次指令覆盖")
        log_path = str(payload.get("_local_log_path") or "")
        self.start_gui_log_viewer(job_id, log_path)
        command = self.tool_command("XBotLegacyRunner.exe", "legacy_runner.py") + [
            "--mode",
            str(mode),
            "--payload-json",
            json.dumps(payload, ensure_ascii=False),
        ]
        if log_path:
            command.extend(["--log-file", log_path])
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        proc = subprocess.Popen(
            command,
            cwd=str(self.app_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        timed_out_event = threading.Event()
        cancel_event = threading.Event()
        preempt_event = threading.Event()

        def kill_on_timeout() -> None:
            if proc.poll() is not None:
                return
            timed_out_event.set()
            self.log_job(job_id, "error", f"任务超过 {timeout_seconds} 秒未结束，已自动停止")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        timeout_timer = threading.Timer(max(60, timeout_seconds), kill_on_timeout)
        timeout_timer.daemon = True
        timeout_timer.start()

        def stop_on_cancel() -> None:
            while proc.poll() is None:
                time.sleep(5)
                cancel_reason = self.job_cancel_reason(job_id)
                if cancel_reason == "preempted_by_mode2":
                    preempt_event.set()
                    cancel_event.set()
                    self.log_job(job_id, "preempted", "任务被模式二抢占，已重新排队，正在停止本地自动化进程")
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                if self.is_job_cancelled(job_id):
                    cancel_event.set()
                    self.log_job(job_id, "error", "任务已被取消，正在停止本地自动化进程")
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break

        cancel_thread = threading.Thread(target=stop_on_cancel, daemon=True)
        cancel_thread.start()
        stdout_lines: list[str] = []
        deadline = time.time() + max(60, timeout_seconds)
        timed_out = False
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.time() > deadline:
                timed_out = True
                proc.terminate()
                break
            stdout_lines.append(line)
            stdout_lines = stdout_lines[-300:]
            event = self.parse_legacy_event(line)
            if event.get("legacy_log"):
                self.forward_legacy_log(job_id, event["legacy_log"])
            if event.get("legacy_result"):
                self.log_job(job_id, "log", "原脚本已返回最终结果，正在汇总。")
        timeout_timer.cancel()
        stderr_text = proc.stderr.read() if proc.stderr is not None else ""
        if timed_out:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        return_code = proc.wait()
        if cancel_event.is_set():
            if preempt_event.is_set():
                reason = "任务被模式二抢占，已重新排队，模式二完成后继续执行"
                self.log_job(job_id, "preempted", reason)
                raise JobPreempted(reason)
            reason = "任务已被计划删除/手动取消，已停止"
            self.log_job(job_id, "error", reason)
            raise RuntimeError(reason)
        if timed_out or timed_out_event.is_set():
            reason = f"任务超过 {timeout_seconds} 秒未结束，已自动停止"
            self.log_job(job_id, "error", reason)
            raise RuntimeError(reason)
        output = "".join(stdout_lines)[-8000:]
        error = (stderr_text or "")[-4000:]
        legacy_result = self.extract_legacy_result(output)
        if legacy_result:
            for line in legacy_result.get("summary_logs", [])[-8:]:
                self.log_job(job_id, "log", line)
            self.log_job(
                job_id,
                "log",
                (
                    f"模式{mode}摘要：窗口={legacy_result.get('started_profiles', 0)}，"
                    f"进入模式={legacy_result.get('entered_mode')}，"
                    f"进入业务循环={legacy_result.get('entered_business_loop')}，"
                    f"耗时={legacy_result.get('duration_seconds')}秒"
                ),
            )
        elif output:
            for line in self.compact_legacy_logs(output)[-8:]:
                self.log_job(job_id, "log", line)
        if return_code != 0:
            if error:
                self.log_job(job_id, "error", self.compact_error(error))
            reason = legacy_result.get("reason") if legacy_result else self.compact_error(error)
            raise RuntimeError(f"原脚本模式{mode}执行失败，退出码={return_code}：{reason}")
        if legacy_result and not legacy_result.get("ok"):
            reason = legacy_result.get("reason") or f"原脚本模式{mode}没有正常完成"
            raise RuntimeError(reason)
        return {
            "status": "legacy_mode_completed",
            "mode": mode,
            "legacy_result": legacy_result or {},
            "stderr_tail": self.compact_error(error) if error else "",
            "local_log_path": log_path,
        }

    def start_gui_log_viewer(self, job_id: int, log_path: str) -> None:
        if not log_path or not self.config.get("open_gui_for_legacy", True):
            return
        command = self.tool_command("XBotGuiLogViewer.exe", "gui_log_viewer.py") + [
            "--log-file",
            log_path,
            "--job-id",
            str(job_id),
        ]
        try:
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(
                command,
                cwd=str(self.app_root()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self.log_job(job_id, "log", "已打开本地 GUI 日志窗口，任务结束后窗口会保留。")
        except Exception as exc:
            self.log_job(job_id, "error", f"打开 GUI 日志窗口失败：{exc}")

    @staticmethod
    def extract_legacy_result(stdout: str) -> Dict[str, Any]:
        for line in reversed((stdout or "").splitlines()):
            data = Worker.parse_legacy_event(line)
            result = data.get("legacy_result")
            if isinstance(result, dict):
                return result
        return {}

    @staticmethod
    def parse_legacy_event(line: str) -> Dict[str, Any]:
        text = (line or "").strip()
        if not text or not text.startswith("{"):
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def compact_legacy_logs(text: str) -> list[str]:
        lines = []
        keywords = [
            "引擎已启动",
            "拉起成功",
            "开始模式",
            ">>> 循环",
            "进度 ->",
            "执行了：",
            "任务全部正常结束",
            "未登录",
            "异常",
            "错误",
        ]
        for raw in (text or "").splitlines():
            line = raw.strip()
            if line and any(keyword in line for keyword in keywords):
                lines.append(line[-500:])
        return lines

    @staticmethod
    def compact_error(text: str) -> str:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return ""
        return " | ".join(lines[-6:])[-900:]

    def forward_legacy_log(self, job_id: int, message: str) -> None:
        now = time.time()
        if message == self._last_forwarded_legacy_log and now - self._last_forwarded_legacy_ts < 30:
            return
        self._last_forwarded_legacy_log = message
        self._last_forwarded_legacy_ts = now
        self.log_job(job_id, "log", message[:900])

    @staticmethod
    def simple_comment_draft(tweet_text: str) -> str:
        if not tweet_text.strip():
            return "这个观点挺有参考价值，想继续看看后续展开。"
        return "这个角度挺实用，尤其是你提到的重点值得继续讨论。"

    def complete(self, job_id: int, result: Dict[str, Any]) -> None:
        api_json(
            "POST",
            f"{self.central_api}/jobs/{job_id}/complete",
            self.token,
            {"node_id": self.node_id, "result": result},
        )

    def fail(self, job_id: int, error: str) -> None:
        api_json(
            "POST",
            f"{self.central_api}/jobs/{job_id}/fail",
            self.token,
            {"node_id": self.node_id, "error": error},
        )

    def report_job_status(
        self,
        action: str,
        job_id: int,
        result: Optional[Dict[str, Any]] = None,
        error: str = "",
    ) -> bool:
        try:
            if action == "complete":
                self.complete(job_id, result or {})
            elif action == "preempt":
                api_json(
                    "POST",
                    f"{self.central_api}/jobs/{job_id}/preempt",
                    self.token,
                    {"node_id": self.node_id, "message": error or "preempted_by_mode2"},
                )
            elif action == "fail":
                self.fail(job_id, error)
            else:
                raise ValueError(f"unsupported report action: {action}")
            return True
        except Exception as exc:
            print(f"job {action} upload failed for job {job_id}: {exc}")
            return False

    def queue_pending_report(
        self,
        action: str,
        job_id: int,
        result: Optional[Dict[str, Any]] = None,
        error: str = "",
    ) -> None:
        self.pending_reports_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "created_at": int(time.time()),
            "node_id": self.node_id,
            "job_id": job_id,
            "action": action,
            "result": result or {},
            "error": error,
        }
        with open(self.pending_reports_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def flush_pending_reports(self) -> None:
        path = self.pending_reports_path
        if not path.exists():
            return
        try:
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception as exc:
            print(f"pending report load failed: {exc}")
            return
        if not records:
            path.unlink(missing_ok=True)
            return
        remaining = []
        for index, record in enumerate(records):
            action = str(record.get("action") or "")
            job_id = int(record.get("job_id") or 0)
            if not job_id or action not in {"complete", "fail", "preempt"}:
                continue
            ok = self.report_job_status(
                action,
                job_id,
                result=record.get("result") or {},
                error=str(record.get("error") or ""),
            )
            if not ok:
                remaining.append(record)
                remaining.extend(records[index + 1 :])
                break
        if remaining:
            tmp_path = path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                for record in remaining:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            tmp_path.replace(path)
        else:
            path.unlink(missing_ok=True)

    def log_job(self, job_id: int, status: str, message: str) -> None:
        if not job_id:
            return
        self.audit.append_log(self._current_log_path, status, message)
        try:
            api_json(
                "POST",
                f"{self.central_api}/jobs/{job_id}/log",
                self.token,
                {"node_id": self.node_id, "status": status, "message": message},
            )
        except Exception as exc:
            print(f"job log upload failed: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local sidecar worker")
    parser.add_argument("--config", default="automation_config.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    worker = Worker(config, args.config)
    worker.run_forever()


if __name__ == "__main__":
    main()
