from __future__ import annotations

import re
from typing import Any, Dict, Optional

from automation.model_client import OpenAICompatibleClient


HIGH_RISK_ACTIONS = {"run_mode", "create_grok_plan_jobs", "create_score_plan_jobs", "auto_schedule_plans", "approve_plan"}


def parse_semantic_intent(text: str, default_group: Optional[str] = None) -> Dict[str, Any]:
    client = OpenAICompatibleClient.from_file()
    if not client.ready():
        return {"action": "unknown", "reason": "模型未启用或配置不完整"}

    prompt = f"""
你是 Discord 自动化机器人的中文指令解析器。只返回 JSON，不要解释。

可用 action:
- run_mode: 执行原脚本模式一/二/三。字段 mode=1/2/3, group_id, target_urls。
- create_grok_plan_jobs: 生成 Grok 计划。字段 period=daily/weekly/monthly, group_id。
- create_score_plan_jobs: 账号评分并制定提升计划。字段 period=daily/weekly/monthly, group_id。
- auto_schedule_plans: 把已有 Grok 计划转成调度。字段 group_id。
- jobs: 查看任务。
- logs: 查看日志。字段 job_id。
- schedule: 查看调度。字段 group_id。
- accounts: 查看账号。字段 group_id。
- groups: 查看分组。
- help: 帮助。
- unknown: 无法识别。

规则：
1. “养号、开始养、跑养号、刷首页、探索页”通常是 run_mode mode=1。
2. “冲贴、冲这个帖子、给这个链接上量”通常是 run_mode mode=2，并提取链接。
3. “发帖、内容矩阵、发布图文”通常是 run_mode mode=3。
4. “账号评分、权重评分、提升到多少分、账号提升计划”通常是 create_score_plan_jobs。
5. “周计划/日计划/月计划/Grok内容计划”通常是 create_grok_plan_jobs。
5. 如果没有分组，但默认分组可用，用默认分组。
6. 高风险动作 run_mode/create_grok_plan_jobs/auto_schedule_plans/approve_plan 必须 needs_confirm=true。

默认分组: {default_group or ""}
用户原文: {text}

返回 JSON 示例：
{{"action":"run_mode","mode":1,"group_id":"测试","target_urls":[],"period":"weekly","needs_confirm":true,"summary":"为测试组执行模式一养号","confidence":0.91}}
"""
    try:
        data = client.chat_json(
            [
                {"role": "system", "content": "你只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
    except Exception as exc:
        return {"action": "unknown", "reason": f"模型识别失败: {exc}"}

    action = str(data.get("action") or "unknown")
    if action == "create_grok_plan_jobs" and re.search(r"账号评分|權重|权重|评分|提升计划|提升方案|账号计划|制定计划", text or "", re.I):
        action = "create_score_plan_jobs"
        data["action"] = action
    if action in HIGH_RISK_ACTIONS:
        data["needs_confirm"] = True
    data.setdefault("target_urls", extract_urls(text))
    if not data.get("group_id") and default_group:
        data["group_id"] = default_group
    return data


def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s，,]+", text or "")
