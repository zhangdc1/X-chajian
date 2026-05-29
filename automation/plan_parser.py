import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from automation.job_types import JOB_COMMENT_DRAFT, JOB_CONTENT_DRAFT, JOB_GENERATE_GROK_PLAN, JOB_MANUAL_REVIEW


SAFE_SCHEDULE_JOB_TYPES = {
    JOB_COMMENT_DRAFT,
    JOB_CONTENT_DRAFT,
    JOB_GENERATE_GROK_PLAN,
    JOB_MANUAL_REVIEW,
}


def build_tasks_from_plan(plan: Dict[str, Any], max_days: int = 7) -> List[Dict[str, Any]]:
    """Build conservative scheduled tasks from a Grok plan draft.

    Grok's raw text is not guaranteed to be structured, so this parser only
    creates safe draft/review jobs. It never creates direct engagement actions.
    """

    raw = plan.get("grok_raw_response") or ""
    account_id = plan.get("account_id") or ""
    period = plan.get("period") or "weekly"
    days = period_to_days(period, max_days=max_days)
    now = datetime.now()
    tasks: List[Dict[str, Any]] = []
    extracted_times = extract_times(raw)
    daily_suggestions = extract_daily_suggestions(raw)

    for index in range(days):
        run_date = now.date() + timedelta(days=index)
        first_time = extracted_times[0] if extracted_times else "10:00"
        second_time = extracted_times[1] if len(extracted_times) > 1 else "20:00"
        suggestion = daily_suggestions[index] if index < len(daily_suggestions) else ""
        topic = suggestion or f"账号 {account_id} 第 {index + 1} 天内容草稿"
        tasks.append(
            {
                "job_type": JOB_CONTENT_DRAFT,
                "run_at": to_ts(run_date, first_time),
                "payload": {
                    "account_id": account_id,
                    "topic": topic[:240],
                    "day_index": index + 1,
                    "daily_suggestion": suggestion,
                    "plan_excerpt": raw[:1000],
                },
            }
        )
        tasks.append(
            {
                "job_type": JOB_MANUAL_REVIEW,
                "run_at": to_ts(run_date, second_time),
                "payload": {
                    "account_id": account_id,
                    "review_type": "daily_plan_review",
                    "plan_excerpt": raw[:1000],
                },
            }
        )
    return tasks


def extract_daily_suggestions(text: str) -> List[str]:
    """Extract Monday-Sunday style daily content suggestions from Grok text."""

    if not text:
        return []
    day_pattern = r"(周[一二三四五六日天]|星期[一二三四五六日天])"
    matches = list(re.finditer(day_pattern, text))
    if not matches:
        return []

    suggestions: List[str] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = clean_suggestion(text[start:end])
        if chunk and chunk not in suggestions:
            suggestions.append(chunk)
    return suggestions[:31]


def clean_suggestion(chunk: str) -> str:
    chunk = re.sub(r"\s+", " ", chunk).strip()
    chunk = re.sub(r"^(每日内容建议|7天循环计划|目标)[:：\s-]*", "", chunk)
    chunk = chunk.strip(" -：:。")
    return chunk[:500]


def period_to_days(period: str, max_days: int = 7) -> int:
    if period == "daily":
        return 1
    if period == "monthly":
        return min(30, max_days)
    if period == "custom":
        return max_days
    return min(7, max_days)


def extract_times(text: str) -> List[str]:
    seen = []
    for hour, minute in re.findall(r"([01]?\d|2[0-3])[:：]([0-5]\d)", text):
        item = f"{int(hour):02d}:{minute}"
        if item not in seen:
            seen.append(item)
    return seen[:4]


def to_ts(run_date: Any, hhmm: str) -> int:
    if isinstance(run_date, str):
        run_date = date.fromisoformat(run_date)
    hour, minute = [int(part) for part in hhmm.split(":", 1)]
    dt = datetime.combine(run_date, datetime.min.time()).replace(hour=hour, minute=minute)
    if dt.timestamp() < time.time():
        dt = dt + timedelta(days=1)
    return int(dt.timestamp())
