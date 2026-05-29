from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse, request
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


from automation.intent_parser import HIGH_RISK_ACTIONS, parse_semantic_intent


BOT_VERSION = "xbot-v2.4"
PENDING_CONFIRMATIONS: Dict[int, Dict[str, Any]] = {}


PERIOD_MAP = {
    "daily": "daily",
    "day": "daily",
    "d": "daily",
    "weekly": "weekly",
    "week": "weekly",
    "w": "weekly",
    "monthly": "monthly",
    "month": "monthly",
    "m": "monthly",
    "日": "daily",
    "周": "weekly",
    "月": "monthly",
}


def pick_value(parts: List[str], keys: set[str]) -> Optional[str]:
    for part in parts:
        for key in keys:
            prefix = f"{key}="
            if part.startswith(prefix):
                return part[len(prefix):].strip()
    return None


def load_config(path: str) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required for discord_config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def http_json(method: str, url: str, token: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = None
    headers = {"X-Automation-Token": token}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = request.Request(url, data=data, headers=headers, method=method)
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError(f"Central API request failed: {method} {url} -> {exc}") from exc


class CentralClient:
    def __init__(self, api: str, token: str):
        self.api = api.rstrip("/")
        self.token = token

    def workers(self) -> Dict[str, Any]:
        return http_json("GET", f"{self.api}/workers", self.token)

    def groups(self) -> Dict[str, Any]:
        return http_json("GET", f"{self.api}/groups", self.token)

    def bind_group_alias(self, alias: str, group_id: str) -> Dict[str, Any]:
        return http_json("POST", f"{self.api}/groups/alias", self.token, {"alias": alias, "group_id": group_id})

    def accounts(self, group_id: Optional[str] = None, node_id: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if group_id:
            query["group_id"] = group_id
        if node_id:
            query["node_id"] = node_id
        return http_json("GET", f"{self.api}/accounts?{parse.urlencode(query)}", self.token)

    def plans(self, group_id: Optional[str] = None, status: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if group_id:
            query["group_id"] = group_id
        if status:
            query["status"] = status
        return http_json("GET", f"{self.api}/plans?{parse.urlencode(query)}", self.token)

    def schedule(self, group_id: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if group_id:
            query["group_id"] = group_id
        return http_json("GET", f"{self.api}/schedule?{parse.urlencode(query)}", self.token)

    def approve_plan(self, plan_id: int, max_days: int = 7, dispatch_now: bool = False) -> Dict[str, Any]:
        return http_json(
            "POST",
            f"{self.api}/plans/{plan_id}/approve",
            self.token,
            {"max_days": max_days, "dispatch_now": dispatch_now},
        )

    def auto_schedule_plans(self, group_id: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
        return http_json(
            "POST",
            f"{self.api}/plans/auto-schedule",
            self.token,
            {"group_id": group_id, "status": status, "max_days": 31, "limit": 100},
        )

    def create_grok_plan_jobs(self, group_id: str, period: str, target_node_id: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
        return http_json(
            "POST",
            f"{self.api}/plans/grok/batch",
            self.token,
            {"group_id": group_id, "period": period, "target_node_id": target_node_id, "limit": limit},
        )

    def run_mode(
        self,
        mode: int,
        group_id: str,
        target_urls: Optional[List[str]] = None,
        target_node_id: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        return http_json(
            "POST",
            f"{self.api}/modes/run",
            self.token,
            {
                "mode": mode,
                "group_id": group_id,
                "target_urls": target_urls or [],
                "target_node_id": target_node_id,
                "limit": limit,
            },
        )

    def jobs(self, status: Optional[str] = None, job_type: Optional[str] = None, node_id: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if status:
            query["status"] = status
        if job_type:
            query["job_type"] = job_type
        if node_id:
            query["node_id"] = node_id
        return http_json("GET", f"{self.api}/jobs?{parse.urlencode(query)}", self.token)

    def job(self, job_id: int) -> Dict[str, Any]:
        return http_json("GET", f"{self.api}/jobs/{job_id}", self.token)

    def job_runs(self, job_id: Optional[int] = None, limit: int = 100) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if job_id is not None:
            query["job_id"] = str(job_id)
        return http_json("GET", f"{self.api}/job-runs?{parse.urlencode(query)}", self.token)

    def create_score_plan_jobs(self, group_id: str, period: str, target_node_id: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
        return http_json(
            "POST",
            f"{self.api}/score-plans/grok/batch",
            self.token,
            {"group_id": group_id, "period": period, "target_node_id": target_node_id, "limit": limit},
        )

    def score_plans(self, group_id: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        query = {"limit": str(limit)}
        if group_id:
            query["group_id"] = group_id
        return http_json("GET", f"{self.api}/score-plans?{parse.urlencode(query)}", self.token)

    def schedule_score_plan(self, plan_id: int) -> Dict[str, Any]:
        return http_json("POST", f"{self.api}/score-plans/{plan_id}/schedule", self.token, {"max_days": 31})


def parse_command(text: str) -> Dict[str, Any]:
    text = re.sub(r"<@!?\d+>", "", text).strip()
    if text.startswith("!"):
        text = text[1:].strip()
    parts = text.split()
    if not parts:
        return {"action": "help"}

    # Compatibility: "A组 绑定 测试 <group_id>" / "B组 绑定 测试 <group_id>"
    if len(parts) >= 4 and (parts[0].lower() in {"agroup", "group"} or parts[0].endswith("组")) and parts[1] in {"绑定", "bind", "alias"}:
        return {"action": "bind_group_alias", "alias": parts[2], "group_id": parts[3]}

    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in {"version", "版本", "查看版本"}:
        return {"action": "version"}
    if cmd in {"确认执行", "执行确认", "confirm"}:
        return {"action": "confirm_execute", "confirm_id": to_int(args[0]) if args else None}
    if cmd in {"help", "h", "?", "帮助", "菜单", "命令"}:
        return {"action": "help"}
    if text in {"查看电脑状态", "电脑状态", "查看状态"} or cmd in {"status", "workers", "电脑", "状态"}:
        return {"action": "workers"}
    if text in {"查看分组", "查询分组", "分组列表", "查看绑定", "查询绑定", "绑定列表", "别名列表"}:
        return {"action": "groups"}
    if cmd in {"groups", "group", "组", "分组", "绑定列表", "别名"}:
        if not args or args[0] in {"list", "ls", "查", "列表"}:
            return {"action": "groups"}
        if args[0] in {"bind", "alias", "绑定", "别名"} and len(args) >= 3:
            return {"action": "bind_group_alias", "alias": args[1], "group_id": args[2]}
        return {"action": "accounts", "group_id": args[0]}
    if cmd in {"bind", "alias", "绑定"} and len(args) >= 2:
        return {"action": "bind_group_alias", "alias": args[0], "group_id": args[1]}
    if cmd in {"accounts", "account", "账号"}:
        group_id = pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None)
        return {"action": "accounts", "group_id": group_id}
    if cmd in {"查看账号", "查询账号"}:
        return {"action": "accounts", "group_id": pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None)}
    if cmd.startswith("生成grok") or cmd.startswith("生成Grok"):
        period = "weekly"
        if "日计划" in cmd:
            period = "daily"
        elif "月计划" in cmd:
            period = "monthly"
        group_id = pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None)
        target_node_id = pick_value(args, {"电脑", "node", "node_id"})
        return {"action": "create_score_plan_jobs", "group_id": group_id, "period": period, "target_node_id": target_node_id, "compat_from": "grok_plan"}
    if cmd in {"账号评分", "评分", "生成账号评分"}:
        period = "weekly"
        joined = " ".join(args)
        if "日" in joined:
            period = "daily"
        elif "月" in joined:
            period = "monthly"
        group_id = pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None)
        return {"action": "create_score_plan_jobs", "group_id": group_id, "period": period, "target_node_id": pick_value(args, {"电脑", "node", "node_id"})}
    if cmd in {"查看账号计划", "账号计划", "查看评分计划"}:
        return {"action": "score_plans", "group_id": pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None)}
    if cmd in {"生成账号调度", "账号调度", "生成评分调度"}:
        return {"action": "schedule_score_plans", "group_id": pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None)}
    if cmd in {"查看草稿计划", "查询草稿计划"}:
        return {"action": "plans", "group_id": pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None), "status": "draft"}
    if cmd in {"查看计划", "查询计划"}:
        return {"action": "plans", "group_id": pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None), "status": None}
    if cmd in {"生成调度", "补调度", "转换调度"}:
        return {"action": "auto_schedule_plans", "group_id": pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None), "status": None}
    if cmd == "确认计划":
        return {"action": "approve_plan", "plan_id": to_int(pick_value(args, {"plan", "计划", "plan_id"}) or (args[0] if args else None)), "dispatch_now": False}
    if cmd == "查看调度":
        return {"action": "schedule", "group_id": pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None)}
    if cmd in {"查看任务", "查询任务"}:
        return {"action": "jobs", "status": None, "job_type": None}
    if cmd in {"查看任务详情", "任务详情", "查询任务详情"}:
        return {"action": "job_detail", "job_id": to_int(pick_value(args, {"任务", "job", "job_id"}) or (args[0] if args else None))}
    if cmd in {"查看日志", "查询日志", "任务日志"}:
        return {"action": "logs", "job_id": to_int(pick_value(args, {"任务", "job", "job_id"}) or (args[0] if args else None))}
    if cmd in {"mode1", "模式1", "模式一"}:
        return parse_mode_command(1, args)
    if cmd in {"mode2", "模式2", "模式二"}:
        return parse_mode_command(2, args)
    if cmd in {"mode3", "模式3", "模式三"}:
        return parse_mode_command(3, args)
    if cmd in {"plan", "plans", "计划"}:
        if not args:
            return {"action": "plans", "group_id": None, "status": None}
        sub = args[0].lower()
        if sub in {"list", "ls", "查", "列表"}:
            return {"action": "plans", "group_id": args[1] if len(args) >= 2 else None, "status": None}
        if sub in {"draft", "草稿"}:
            return {"action": "plans", "group_id": args[1] if len(args) >= 2 else None, "status": "draft"}
        if sub in {"approve", "确认"} and len(args) >= 2:
            return {"action": "approve_plan", "plan_id": to_int(args[1]), "dispatch_now": False}
        if sub in {"schedule", "auto-schedule", "调度", "补调度", "转换"}:
            return {"action": "auto_schedule_plans", "group_id": args[1] if len(args) >= 2 else None, "status": None}
        period = PERIOD_MAP.get(sub, "weekly")
        group_id = args[1] if sub in PERIOD_MAP and len(args) >= 2 else args[0]
        target_node_id = args[2] if sub in PERIOD_MAP and len(args) >= 3 else None
        return {"action": "create_score_plan_jobs", "group_id": group_id, "period": period, "target_node_id": target_node_id, "compat_from": "plan"}
    if cmd in {"jobs", "job", "任务"}:
        if args and args[0] in {"detail", "详情"} and len(args) >= 2:
            return {"action": "job_detail", "job_id": to_int(args[1])}
        return {"action": "jobs", "status": None, "job_type": None}
    if cmd in {"logs", "log", "日志"}:
        return {"action": "logs", "job_id": to_int(args[0]) if args else None}
    if cmd in {"schedule", "调度"}:
        return {"action": "schedule", "group_id": args[0] if args else None}
    return {"action": "unknown", "text": text}


def parse_mode_command(mode: int, args: List[str]) -> Dict[str, Any]:
    group_id = pick_value(args, {"分组", "group", "group_id"}) or (args[0] if args else None)
    target_node_id = pick_value(args, {"电脑", "node", "node_id"})
    urls: List[str] = []
    raw_url_value = pick_value(args, {"url", "urls", "链接"})
    if raw_url_value:
        urls.extend([item.strip() for item in re.split(r"[,，|]", raw_url_value) if item.strip()])
    urls.extend([item for item in args if item.startswith("http://") or item.startswith("https://")])
    return {
        "action": "run_mode",
        "mode": mode,
        "group_id": group_id,
        "target_node_id": target_node_id,
        "target_urls": urls,
    }


def to_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def cn_job_type(job_type: Optional[str]) -> str:
    return {
        "generate_grok_plan": "生成Grok计划",
        "parse_plan": "解析计划",
        "content_draft": "内容草稿",
        "comment_draft": "评论草稿",
        "manual_review": "人工复核",
        "legacy_mode_run": "原脚本模式执行",
        "score_grok_plan": "账号评分计划",
    }.get(job_type or "", job_type or "未知")


def cn_status(status: Optional[str]) -> str:
    return {
        "queued": "排队中",
        "leased": "已领取/执行中",
        "running": "执行中",
        "completed": "已完成",
        "failed": "失败",
        "scheduled": "已调度",
        "dispatched": "已派发",
        "draft": "草稿",
        "approved": "已确认",
        "auto_scheduled": "已自动调度",
        "superseded": "已被新计划替换",
        "skipped_expired": "已跳过/时间过期",
        "cancelled_by_new_plan": "已被新计划取消",
        "cancelled": "已取消",
        "paused": "已暂停",
        "error": "错误",
        "log": "日志",
    }.get(status or "", status or "未知")


def short_json(data: Dict[str, Any], limit: int = 300) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text[:limit] + ("..." if len(text) > limit else "")


def build_help() -> str:
    return (
        f"X-bot 中文指令 {BOT_VERSION}：\n"
        "!帮助\n"
        "!查看版本\n"
        "!查看电脑状态\n"
        "!查看分组\n"
        "!绑定 测试 4028808a9dddd516019df3bd0162204d\n"
        "!查看账号 测试\n"
        "!账号评分 测试\n"
        "!账号评分 测试 周计划\n"
        "!查看账号计划 测试\n"
        "!生成账号调度 测试\n"
        "!查看计划 测试\n"
        "!查看草稿计划 测试\n"
        "!生成调度 测试  （把已有Grok计划转成调度）\n"
        "!确认计划 123\n"
        "!模式一 测试\n"
        "!模式二 测试 链接=https://x.com/xxx/status/123\n"
        "!模式三 测试\n"
        "!查看任务\n"
        "!查看任务详情 123\n"
        "!查看日志 123\n"
        "!查看调度 测试\n"
        "!确认执行 123  （确认自然语言识别出来的高风险动作）\n"
        "自然语言示例：现在开始养号 / 帮测试组跑模式一 / 开始冲这个帖子 https://x.com/...\n"
        "也兼容：@Bot A组 绑定 测试 4028808a9dddd516019df3bd0162204d"
    )


def format_response(command: Dict[str, Any], client: CentralClient) -> Tuple[str, List[int]]:
    action = command["action"]
    if action == "confirm_execute":
        confirm_id = command.get("confirm_id")
        pending = PENDING_CONFIRMATIONS.pop(int(confirm_id or 0), None)
        if not pending:
            return "没有找到这个确认编号，可能已过期或已经执行。", []
        return format_response(pending["command"], client)
    if action == "version":
        return f"当前运行版本: {BOT_VERSION}", []
    if action == "help":
        return build_help(), []
    if action == "workers":
        workers = client.workers().get("workers", [])
        if not workers:
            return "当前没有在线 worker。", []
        lines = ["电脑状态:"]
        for item in workers:
            meta = item.get("meta") or {}
            groups = ",".join(meta.get("sync_group_ids") or []) or "-"
            grok = "开启" if meta.get("enable_grok_browser") else "关闭"
            lines.append(f"- {item.get('node_id')} | {item.get('label')} | {item.get('status')} | Grok={grok} | groups={groups}")
        return "\n".join(lines), []
    if action == "groups":
        groups = client.groups().get("groups", [])
        if not groups:
            return "没有分组记录，请先启动 worker 同步账号。", []
        lines = ["分组列表:"]
        for item in groups:
            alias = item.get("alias") or "-"
            lines.append(f"- alias={alias} | group_id={item.get('group_id')} | accounts={item.get('account_count')}")
        return "\n".join(lines), []
    if action == "bind_group_alias":
        alias, group_id = command.get("alias"), command.get("group_id")
        if not alias or not group_id:
            return "用法: !bind 测试 group_id", []
        client.bind_group_alias(alias, group_id)
        return f"已绑定: {alias} -> {group_id}\n查询: !groups", []
    if action == "accounts":
        accounts = client.accounts(command.get("group_id")).get("accounts", [])
        if not accounts:
            return "没有查到账号。请先用 !groups 确认别名或 group_id。", []
        lines = [f"账号数: {len(accounts)}"]
        for item in accounts:
            lines.append(f"- {item.get('profile_name')} | profile={item.get('profile_id')} | node={item.get('node_id')}")
        return "\n".join(lines), []
    if action == "create_grok_plan_jobs":
        group_id = command.get("group_id")
        if not group_id:
            return "用法: !账号评分 测试", []
        data = client.create_score_plan_jobs(group_id, command.get("period") or "weekly", command.get("target_node_id"))
        job_ids = [int(x) for x in data.get("job_ids", [])]
        if not job_ids:
            return "没有创建账号评分任务。请用 !查看账号 测试 确认分组内有账号。", []
        return (
            f"已按账号评分流程创建任务 {len(job_ids)} 个。\n"
            f"任务ID: {', '.join(str(x) for x in job_ids)}\n"
            f"已清理旧计划 {data.get('cleaned_plans', 0)} 个，旧调度任务 {data.get('cleaned_tasks', 0)} 个。\n"
            f"本次会读取中央评分提示词，完成后自动生成账号专属调度。"
        ), job_ids
    if action == "create_score_plan_jobs":
        group_id = command.get("group_id")
        if not group_id:
            return "用法: !账号评分 测试", []
        data = client.create_score_plan_jobs(group_id, command.get("period") or "weekly", command.get("target_node_id"))
        job_ids = [int(x) for x in data.get("job_ids", [])]
        if not job_ids:
            return "没有创建账号评分任务。请用 !查看账号 测试 确认分组内有账号。", []
        return (
            f"已创建账号评分任务 {len(job_ids)} 个。\n"
            f"任务ID: {', '.join(str(x) for x in job_ids)}\n"
            f"已清理旧计划 {data.get('cleaned_plans', 0)} 个，旧调度任务 {data.get('cleaned_tasks', 0)} 个。\n"
            f"完成后会自动生成账号专属调度。"
        ), job_ids
    if action == "score_plans":
        plans = client.score_plans(command.get("group_id")).get("plans", [])
        if not plans:
            return "没有查到账号评分计划。", []
        lines = [f"账号评分计划数: {len(plans)}"]
        for item in plans[:20]:
            summary = item.get("task_summary") or {}
            lines.append(
                f"- plan={item.get('id')} account={item.get('account_id')} score={item.get('score') or '-'} "
                f"status={cn_status(item.get('status'))} tasks={summary.get('total', 0)}"
            )
        return "\n".join(lines), []
    if action == "schedule_score_plans":
        plans = client.score_plans(command.get("group_id"), limit=100).get("plans", [])
        total = 0
        plan_ids = []
        for plan in plans:
            data = client.schedule_score_plan(int(plan["id"]))
            count = int(data.get("scheduled_count", 0) or 0)
            if count:
                total += count
                plan_ids.append(int(plan["id"]))
        return f"已生成账号调度任务 {total} 个。\n涉及计划: {', '.join(str(x) for x in plan_ids) or '无'}", []
    if action == "plans":
        plans = client.plans(command.get("group_id"), command.get("status")).get("plans", [])
        if not plans:
            return "没有查到计划记录。", []
        lines = [f"计划数: {len(plans)}"]
        for item in plans:
            lines.append(f"- plan={item.get('id')} account={item.get('account_id')} period={item.get('period')} status={cn_status(item.get('status'))} source_job={item.get('source_job_id')}")
        return "\n".join(lines), []
    if action == "approve_plan":
        plan_id = command.get("plan_id")
        if not plan_id:
            return "用法: !plan approve 123", []
        data = client.approve_plan(plan_id)
        return f"计划 {plan_id} 已确认，生成调度任务 {data.get('scheduled_count', 0)} 个。", []
    if action == "auto_schedule_plans":
        data = client.auto_schedule_plans(command.get("group_id"), command.get("status"))
        return (
            f"已转换 Grok 计划为调度任务：{data.get('scheduled_count', 0)} 个。\n"
            f"涉及计划: {', '.join(str(x) for x in data.get('plan_ids', [])) or '无'}\n"
            f"跳过: {data.get('skipped', 0)} 个。"
        ), []
    if action == "run_mode":
        mode = int(command.get("mode") or 0)
        group_id = command.get("group_id")
        if not group_id:
            return f"用法: !mode{mode or 1} 测试", []
        if mode == 2 and not command.get("target_urls"):
            return "模式二需要目标链接。用法: !mode2 测试 url=https://x.com/xxx/status/123", []
        data = client.run_mode(
            mode=mode,
            group_id=group_id,
            target_urls=command.get("target_urls") or [],
            target_node_id=command.get("target_node_id"),
        )
        job_ids = [int(x) for x in data.get("job_ids", [])]
        if not job_ids:
            return "没有创建模式任务。请用 !groups / !accounts 测试 确认分组内有账号和在线电脑。", []
        return f"已创建模式{mode}任务 {len(job_ids)} 个。\n任务ID: {', '.join(str(x) for x in job_ids)}\n我只汇报开始、日志路径和最终结果。", job_ids
    if action == "jobs":
        jobs = client.jobs(command.get("status"), command.get("job_type")).get("jobs", [])
        if not jobs:
            return "没有查到任务。", []
        lines = [f"任务数: {len(jobs)}"]
        for item in jobs:
            lines.append(f"- job={item.get('id')} type={cn_job_type(item.get('job_type'))} status={cn_status(item.get('status'))} node={item.get('target_node_id') or item.get('leased_by')}")
        return "\n".join(lines), []
    if action == "job_detail":
        job_id = command.get("job_id")
        if not job_id:
            return "用法: !job detail 123", []
        job = client.job(job_id).get("job")
        if not job:
            return f"没有查到任务 {job_id}。", []
        return (
            f"任务 {job.get('id')}\n"
            f"类型: {cn_job_type(job.get('job_type'))}\n"
            f"状态: {cn_status(job.get('status'))}\n"
            f"目标电脑: {job.get('target_node_id')}\n"
            f"执行电脑: {job.get('leased_by')}\n"
            f"错误: {job.get('error') or '无'}\n"
            f"结果: {short_json(job.get('result') or {}, 500)}"
        ), []
    if action == "logs":
        runs = client.job_runs(command.get("job_id")).get("runs", [])
        if not runs:
            return "没有查到日志。", []
        lines = [f"日志数: {len(runs)}"]
        for item in runs[:20]:
            lines.append(f"- job={item.get('job_id')} node={item.get('node_id')} status={cn_status(item.get('status'))} | {item.get('message')}")
        return "\n".join(lines), []
    if action == "schedule":
        tasks = client.schedule(command.get("group_id")).get("tasks", [])
        if not tasks:
            return "没有查到调度任务。", []
        lines = [f"调度任务数: {len(tasks)}"]
        for item in tasks:
            lines.append(f"- task={item.get('id')} account={item.get('account_id')} job={cn_job_type(item.get('job_type'))} status={cn_status(item.get('status'))}")
        return "\n".join(lines), []
    return "我没理解这条命令。请输入 !help 查看固定命令。", []


async def discord_preflight(token: str, proxy: Optional[str], attempts: int = 30) -> None:
    import aiohttp

    last_error: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {"Authorization": f"Bot {token}"}
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://discord.com/api/v10/users/@me", proxy=proxy, headers=headers) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        raise RuntimeError(f"Discord token/API preflight failed: HTTP {resp.status} {text[:300]}")
                    data = json.loads(text)
                    print(f"Discord API preflight ok: bot={data.get('username')} id={data.get('id')}")
                async with session.ws_connect("wss://gateway.discord.gg/?v=10&encoding=json", proxy=proxy, timeout=20) as ws:
                    msg = await ws.receive(timeout=20)
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        raise RuntimeError(f"Discord gateway preflight failed: message type {msg.type}")
                    print("Discord gateway preflight ok")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            delay = min(30, 3 * attempt)
            print(f"Discord preflight attempt {attempt}/{attempts} failed: {type(exc).__name__}: {exc}")
            print(f"Retrying Discord preflight in {delay}s...")
            await asyncio.sleep(delay)
    raise RuntimeError(f"Discord preflight failed after retries: {last_error}")


def central_preflight(api: str, token: str) -> None:
    url = f"{api.rstrip('/')}/health"
    print(f"Central preflight: {url}")
    last_error = None
    for attempt in range(1, 11):
        try:
            data = http_json("GET", url, token)
            if not data.get("ok"):
                raise RuntimeError(f"Central preflight failed: {data}")
            print("Central preflight ok")
            return
        except Exception as exc:
            last_error = exc
            print(f"Central preflight attempt {attempt}/10 failed: {exc}")
            time.sleep(2)
    raise RuntimeError(f"Central preflight failed after retries: {last_error}")


async def watch_job(channel: Any, client: CentralClient, job_id: int) -> None:
    last_status = None
    seen_run_ids: set[int] = set()
    for _ in range(120):
        await asyncio.sleep(5)
        try:
            job = client.job(job_id).get("job") or {}
        except Exception as exc:
            await channel.send(f"任务 {job_id} 查询失败: {exc}")
            return
        status = job.get("status")
        if status and status != last_status:
            last_status = status
            await channel.send(f"任务 {job_id} 状态: {cn_status(status)} | {cn_job_type(job.get('job_type'))}")
        try:
            runs = client.job_runs(job_id).get("runs", [])
        except Exception:
            runs = []
        for item in reversed(runs):
            run_id = int(item.get("id"))
            if run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)
            message = str(item.get("message") or "")
            status_name = str(item.get("status") or "")
            if should_send_job_run(job.get("job_type"), status_name, message):
                await channel.send(f"任务 {job_id}: {message}"[:1900])
        if status in {"completed", "failed"}:
            if status == "completed":
                await channel.send(summarize_completed_job(job)[:1900])
            else:
                await channel.send(summarize_failed_job(job)[:1900])
            return
    await channel.send(f"任务 {job_id} 仍未结束。可用 !job detail {job_id} 或 !logs {job_id} 查看。")


def should_send_job_run(job_type: Optional[str], status: str, message: str) -> bool:
    if "本地日志文件" in message or "已打开本地 GUI" in message:
        return True
    if job_type == "legacy_mode_run":
        return status in {"error", "failed"}
    return status in {"error", "failed"} or "完成" in message


def summarize_completed_job(job: Dict[str, Any]) -> str:
    job_id = job.get("id")
    job_type = job.get("job_type")
    result = job.get("result") or {}
    payload = job.get("payload") or {}
    local_log = result.get("local_log_path") or (result.get("legacy_result") or {}).get("local_log_path") or ""
    if job_type == "score_grok_plan":
        parsed = result.get("parsed_plan") or {}
        days = parsed.get("days") or []
        slot_count = sum(len(day.get("slots") or []) for day in days)
        if result.get("status") == "stub":
            status_line = "未启用 Grok 浏览器，仅生成规则兜底计划"
            suggestion = "下一步：把 worker 的 enable_grok_browser 改为 true 后重启 worker。"
        else:
            status_line = "已完成 Grok 账号评分"
            suggestion = "已自动转换为账号专属调度。"
        return (
            f"任务 {job_id} 完成：账号评分计划\n"
            f"账号: {result.get('profile_id') or payload.get('profile_id') or '-'}\n"
            f"结果: {status_line}\n"
            f"评分: {parsed.get('score') if parsed.get('score') is not None else '-'} | 解析: {parsed.get('parser', '-')}\n"
            f"计划: {len(days)} 天 / {slot_count} 个时间点\n"
            f"{suggestion}\n"
            f"本地日志: {local_log or '-'}"
        )
    if job_type == "legacy_mode_run":
        legacy = result.get("legacy_result") or {}
        return (
            f"任务 {job_id} 完成：模式{result.get('mode') or payload.get('mode')}\n"
            f"执行电脑: {job.get('leased_by') or '-'}\n"
            f"窗口: {legacy.get('started_profiles', '-')}, 耗时: {legacy.get('duration_seconds', '-')}秒\n"
            f"状态: {'正常完成' if legacy.get('ok', True) else legacy.get('status', '异常')}\n"
            f"本地日志: {local_log or '-'}"
        )
    return (
        f"任务 {job_id} 完成：{cn_job_type(job_type)}\n"
        f"状态: {result.get('status', 'completed')}\n"
        f"本地日志: {local_log or '-'}"
    )


def summarize_failed_job(job: Dict[str, Any]) -> str:
    error = job.get("error") or "未知错误"
    return (
        f"任务 {job.get('id')} 失败：{cn_job_type(job.get('job_type'))}\n"
        f"原因: {error[:500]}\n"
        f"建议: 用 !查看日志 {job.get('id')} 查看详细日志。"
    )


def maybe_semantic_command(command: Dict[str, Any], raw_text: str, config: Dict[str, Any]) -> Dict[str, Any]:
    if command.get("action") != "unknown":
        if command.get("action") == "create_grok_plan_jobs" and is_score_plan_text(raw_text):
            command["action"] = "create_score_plan_jobs"
            command["compat_from"] = "score_keywords"
        return command
    default_group = config.get("default_group") or config.get("default_group_id")
    semantic = parse_semantic_intent(raw_text, default_group=default_group)
    if semantic.get("action") == "create_grok_plan_jobs" and is_score_plan_text(raw_text):
        semantic["action"] = "create_score_plan_jobs"
        semantic["compat_from"] = "score_keywords"
    semantic["semantic"] = True
    return semantic


def is_score_plan_text(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "").lower()
    score_keywords = ("账号评分", "權重", "权重", "评分", "提升计划", "提升方案", "账号计划", "制定计划")
    return any(keyword in normalized for keyword in score_keywords)


def build_confirmation(command: Dict[str, Any]) -> str:
    confirm_id = int(time.time() * 1000) % 1000000
    PENDING_CONFIRMATIONS[confirm_id] = {"command": command, "created_at": time.time()}
    summary = command.get("summary") or summarize_command(command)
    return (
        f"我识别到高风险动作，需要你确认：\n"
        f"{summary}\n"
        f"确认编号：{confirm_id}\n"
        f"发送：!确认执行 {confirm_id}"
    )


def summarize_command(command: Dict[str, Any]) -> str:
    action = command.get("action")
    if action == "run_mode":
        urls = command.get("target_urls") or []
        url_text = f"，链接 {len(urls)} 条" if urls else ""
        return f"执行模式{command.get('mode')}，分组={command.get('group_id')}{url_text}"
    if action == "create_grok_plan_jobs":
        return f"兼容旧 Grok 计划命令，实际执行账号评分并生成 {command.get('period') or 'weekly'} 提升计划，分组={command.get('group_id')}"
    if action == "create_score_plan_jobs":
        return f"执行账号评分并生成 {command.get('period') or 'weekly'} 提升计划，分组={command.get('group_id')}"
    if action == "auto_schedule_plans":
        return f"把 Grok 计划转换为调度，分组={command.get('group_id')}"
    return f"执行动作：{action}"


async def run_bot(config: Dict[str, Any]) -> None:
    try:
        import discord
    except ImportError as exc:
        raise RuntimeError("discord.py is not installed. Install it before running the Discord bot.") from exc

    central = CentralClient(config["central_api"], config["central_token"])
    proxy = (config.get("proxy") or "").strip() or None
    print(f"Discord proxy: {proxy or '(none)'}")
    print(f"Central API: {config.get('central_api')}")
    central_preflight(config["central_api"], config["central_token"])
    await discord_preflight(config["discord_token"], proxy)

    prefix = config.get("command_prefix", "!")
    allowed = set(int(x) for x in config.get("allowed_user_ids", []) if str(x).isdigit())

    reconnect_attempt = 0
    while True:
        reconnect_attempt += 1
        client = build_discord_client(discord, central, prefix, allowed, proxy, config)
        try:
            print(f"Discord bot connecting... attempt={reconnect_attempt}")
            await client.start(config["discord_token"], reconnect=True)
            reconnect_attempt = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            delay = min(60, 5 * reconnect_attempt)
            print(f"Discord bot connection failed: {type(exc).__name__}: {exc}")
            print(f"Will retry Discord connection in {delay}s.")
            await asyncio.sleep(delay)
        finally:
            try:
                if not client.is_closed():
                    await client.close()
            except Exception:
                pass


def build_discord_client(discord: Any, central: CentralClient, prefix: str, allowed: set[int], proxy: Optional[str], config: Dict[str, Any]) -> Any:
    intents = discord.Intents.default()
    intents.message_content = True
    options: Dict[str, Any] = {"intents": intents}
    if proxy:
        options["proxy"] = proxy
    client = discord.Client(**options)

    @client.event
    async def on_ready() -> None:
        print(f"Discord bot logged in as {client.user} ({BOT_VERSION})")

    @client.event
    async def on_disconnect() -> None:
        print("Discord bot disconnected, discord.py will reconnect if possible.")

    @client.event
    async def on_resumed() -> None:
        print("Discord bot session resumed.")

    @client.event
    async def on_message(message: Any) -> None:
        if message.author.bot:
            return
        if allowed and int(message.author.id) not in allowed:
            return
        content = message.content.strip()
        mentioned = client.user in message.mentions if client.user else False
        if not mentioned and not content.startswith(prefix):
            return
        if content.startswith(prefix):
            content = content[len(prefix):].strip()
        raw_content = content
        command = maybe_semantic_command(parse_command(content), raw_content, config)
        if command.get("semantic") and command.get("action") in HIGH_RISK_ACTIONS and command.get("needs_confirm", True):
            await message.channel.send(build_confirmation(command)[:1900])
            return
        try:
            response, job_ids = format_response(command, central)
        except Exception as exc:
            response, job_ids = f"执行失败: {exc}", []
        await message.channel.send(response[:1900])
        for job_id in job_ids:
            asyncio.create_task(watch_job(message.channel, central, job_id))

    return client


def main() -> None:
    parser = argparse.ArgumentParser(description="Discord control bot")
    parser.add_argument("--config", default="discord_config.yaml")
    parser.add_argument("--parse-only", default=None)
    args = parser.parse_args()
    if args.parse_only is not None:
        print(json.dumps(parse_command(args.parse_only), ensure_ascii=False, indent=2))
        return
    asyncio.run(run_bot(load_config(args.config)))


if __name__ == "__main__":
    main()
