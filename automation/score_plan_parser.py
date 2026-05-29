from __future__ import annotations

import json
import re
import hashlib
import random
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from automation.job_types import JOB_LEGACY_MODE_RUN
from automation.model_client import OpenAICompatibleClient


SLOT_TIMES = ["09:00", "12:00", "15:00", "18:00", "21:00"]
METRIC_KEYS = {
    "likes": "点赞量",
    "bookmarks": "收藏量",
    "retweets": "转帖量",
    "replies": "评论量",
    "follows": "关注量",
    "posts": "发帖量",
    "manual_searches": "手动搜索量",
}


def parse_score_plan(raw: str, period: str = "weekly") -> Dict[str, Any]:
    raw = raw or ""
    parsed = parse_markdown_tables(raw)
    if not parsed:
        parsed = parse_plain_metric_tables(raw)
    parser = "table"
    if not parsed:
        parsed = parse_with_model(raw)
        parser = "model" if parsed else "rules"
    if not parsed:
        parsed = parse_by_rules(raw, period)
        parser = "rules"
    elif not has_actionable_metrics(parsed):
        parsed = parse_by_rules(raw, period)
        parser = "rules"
    return {
        "type": "score_plan",
        "parser": parser,
        "period": period,
        "score": extract_score(raw),
        "days": normalize_days(parsed, period),
        "raw_excerpt": raw[:2000],
    }


def build_tasks_from_score_plan(plan: Dict[str, Any], max_days: int = 31) -> List[Dict[str, Any]]:
    parsed = plan.get("parsed_plan") or {}
    days = (parsed.get("days") or [])[:max_days]
    tasks: List[Dict[str, Any]] = []
    account_id = plan.get("account_id") or ""
    group_id = plan.get("group_id") or ""
    for day in days:
        run_date = day.get("date") or date.today().isoformat()
        for slot in day.get("slots") or []:
            metrics = normalize_metrics(slot.get("metrics") or {})
            target_urls = slot.get("target_urls") or []
            if not (has_interaction(metrics) or int(metrics.get("posts") or 0) > 0 or target_urls):
                continue
            run_at = to_ts(run_date, slot.get("time") or "09:00", skip_expired=True)
            base_payload = {
                "account_id": account_id,
                "profile_id": account_id,
                "group_id": group_id,
                "plan_id": plan.get("id"),
                "slot_time": f"{run_date} {slot.get('time') or '09:00'}",
                "metrics": metrics,
                "source": "grok_score_plan",
            }
            if run_at is None:
                tasks.append(
                    {
                        "job_type": JOB_LEGACY_MODE_RUN,
                        "run_at": int(time.time()),
                        "status": "skipped_expired",
                        "payload": {
                            **base_payload,
                            "mode": 1,
                            "skip_reason": "当天时间点已过，按规则跳过，不顺延到第二天",
                        },
                    }
                )
                continue
            if has_interaction(metrics):
                tasks.append(
                    {
                        "job_type": JOB_LEGACY_MODE_RUN,
                        "run_at": run_at,
                        "payload": {
                            **base_payload,
                            "mode": 1,
                            "config_overrides": farming_overrides(metrics),
                        },
                    }
                )
            if int(metrics.get("posts") or 0) > 0:
                tasks.append(
                    {
                        "job_type": JOB_LEGACY_MODE_RUN,
                        "run_at": run_at + 60,
                        "payload": {
                            **base_payload,
                            "mode": 3,
                            "config_overrides": {"POST_CONFIG": {"post_count": int(metrics.get("posts") or 1)}},
                        },
                    }
                )
            for url in target_urls:
                tasks.append(
                    {
                        "job_type": JOB_LEGACY_MODE_RUN,
                        "run_at": run_at + 120,
                        "payload": {
                            **base_payload,
                            "mode": 2,
                            "target_urls": [url],
                            "config_overrides": boost_overrides(metrics),
                        },
                    }
                )
    if has_actionable_metrics(days):
        return tasks
    fallback_period = parsed.get("period") or "weekly"
    fallback_days = normalize_days(parse_by_rules(str(plan.get("raw_excerpt") or ""), fallback_period), fallback_period)
    return build_tasks_from_score_plan({**plan, "parsed_plan": {**parsed, "days": fallback_days}}, max_days=max_days)


def parse_markdown_tables(raw: str) -> List[Dict[str, Any]]:
    days: List[Dict[str, Any]] = []
    current_day = None
    for line in raw.splitlines():
        day_match = re.search(r"第\s*([1-9]\d*)\s*天|Day\s*([1-9]\d*)|周[一二三四五六日天]", line, re.I)
        if day_match:
            current_day = len(days) + 1
        if "|" not in line or not re.search(r"\d{1,2}[:：]\d{2}", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 8:
            continue
        nums = [to_int(c) for c in cells[1:8]]
        slot = {
            "time": normalize_time(cells[0]),
            "metrics": dict(zip(METRIC_KEYS.keys(), nums)),
        }
        day_index = current_day or len(days) + 1
        ensure_day(days, day_index)["slots"].append(slot)
    return days


def parse_with_model(raw: str) -> List[Dict[str, Any]]:
    client = OpenAICompatibleClient.from_file()
    if not client.ready() or not raw.strip():
        return []
    prompt = (
        "把下面的账号权重提升计划转换为 JSON。只输出 JSON，格式为："
        '{"days":[{"day_index":1,"slots":[{"time":"09:00","metrics":{"likes":1,"bookmarks":1,'
        '"retweets":1,"replies":1,"follows":1,"posts":0,"manual_searches":1},"target_urls":[]}]}]}。'
        "缺失数值填 0，时间统一为 09:00/12:00/15:00/18:00/21:00。\n\n"
        + raw[:8000]
    )
    try:
        data = client.chat_json(
            [{"role": "system", "content": "你只输出合法 JSON。"}, {"role": "user", "content": prompt}],
            temperature=0.1,
        )
    except Exception:
        return []
    return data.get("days") if isinstance(data.get("days"), list) else []


def parse_plain_metric_tables(raw: str) -> List[Dict[str, Any]]:
    days: List[Dict[str, Any]] = []
    current_day = None
    pending_time = None
    pending_nums: List[int] = []

    def flush() -> None:
        nonlocal pending_time, pending_nums
        if not pending_time or len(pending_nums) < 7:
            return
        day_index = current_day or len(days) + 1
        ensure_day(days, day_index)["slots"].append(
            {
                "time": normalize_time(pending_time),
                "metrics": dict(zip(METRIC_KEYS.keys(), pending_nums[:7])),
            }
        )
        pending_time = None
        pending_nums = []

    for line in (raw or "").splitlines():
        text = line.strip()
        if not text:
            continue
        day_match = re.search(r"(?:第\s*([1-9]\d*)\s*天|Day\s*([1-9]\d*))", text, re.I)
        if day_match:
            flush()
            current_day = int(day_match.group(1) or day_match.group(2) or len(days) + 1)
            continue
        row_parts = re.split(r"\s+", text)
        if row_parts and re.fullmatch(r"([01]?\d|2[0-3])[:：]([0-5]\d)", row_parts[0] or ""):
            nums = [to_int(item) for item in row_parts[1:] if re.fullmatch(r"-?\d+", item or "")]
            if len(nums) >= 7:
                flush()
                day_index = current_day or len(days) + 1
                ensure_day(days, day_index)["slots"].append(
                    {
                        "time": normalize_time(row_parts[0]),
                        "metrics": dict(zip(METRIC_KEYS.keys(), nums[:7])),
                    }
                )
                continue
        time_match = re.fullmatch(r"([01]?\d|2[0-3])[:：]([0-5]\d)", text)
        if time_match:
            flush()
            pending_time = text
            pending_nums = []
            continue
        if pending_time and re.fullmatch(r"-?\d+", text):
            pending_nums.append(to_int(text))
            if len(pending_nums) >= 7:
                flush()
    flush()
    return days


def parse_by_rules(raw: str, period: str) -> List[Dict[str, Any]]:
    days_count = 1 if period == "daily" else 7
    rng = random.Random(int(hashlib.sha256(f"{period}|{raw[:1000]}".encode("utf-8")).hexdigest()[:16], 16))
    days = []
    metric_ranges = {
        "likes": (12, 18),
        "bookmarks": (1, 4),
        "retweets": (0, 3),
        "replies": (1, 3),
        "follows": (0, 2),
        "manual_searches": (1, 3),
    }
    active_slots = {"09:00", "12:00", "15:00", "18:00", "21:00"}
    for day_index in range(1, days_count + 1):
        slots = []
        progress = 0 if days_count <= 1 else (day_index - 1) / max(1, days_count - 1)
        day_trend = 0.88 + progress * 0.18
        post_slots = set()
        if day_index % 2 == 1:
            post_slots.add("12:00")
        if rng.random() > 0.45 or day_index in {1, days_count}:
            post_slots.add("18:00")
        if rng.random() > 0.75:
            post_slots.add("15:00")
        for slot_time in SLOT_TIMES:
            slot_bias = {
                "09:00": -0.06,
                "12:00": 0.04,
                "15:00": 0.02,
                "18:00": 0.05,
                "21:00": -0.01,
            }.get(slot_time, 0.0)
            slots.append(
                {
                    "time": slot_time,
                    "metrics": {
                        "likes": vary_metric(rng, metric_ranges["likes"], day_trend + slot_bias, slot_time),
                        "bookmarks": vary_metric(rng, metric_ranges["bookmarks"], day_trend + slot_bias * 0.8, slot_time),
                        "retweets": vary_metric(rng, metric_ranges["retweets"], day_trend + slot_bias * 0.6, slot_time),
                        "replies": vary_metric(rng, metric_ranges["replies"], day_trend + slot_bias * 0.7, slot_time),
                        "follows": vary_metric(rng, metric_ranges["follows"], day_trend + slot_bias * 0.5, slot_time),
                        "posts": 1 if slot_time in post_slots else 0,
                        "manual_searches": vary_metric(rng, metric_ranges["manual_searches"], day_trend + slot_bias * 0.3, slot_time),
                    },
                }
            )
        days.append({"day_index": day_index, "slots": slots})
    return days


def vary_metric(rng: random.Random, bounds: tuple[int, int], day_factor: float, slot_time: str) -> int:
    low, high = bounds
    base = rng.randint(low, high)
    slot_adjust = {
        "09:00": -1,
        "12:00": 1,
        "15:00": 0,
        "18:00": 1,
        "21:00": -1,
    }.get(slot_time, 0)
    spread = max(1, high - low)
    trend_adjust = int(round((day_factor - 1.0) * spread))
    jitter = rng.randint(-1, 1)
    return max(low, min(high, base + trend_adjust + slot_adjust + jitter))


def normalize_days(days: List[Dict[str, Any]], period: str) -> List[Dict[str, Any]]:
    count = 1 if period == "daily" else 30 if period == "monthly" else 7
    result = []
    today = date.today()
    for idx in range(count):
        source = days[idx] if idx < len(days) else {"slots": []}
        slots = normalize_slots(source.get("slots") or [])
        result.append({"day_index": idx + 1, "date": (today + timedelta(days=idx)).isoformat(), "slots": slots})
    return result


def normalize_slots(slots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_time = {normalize_time(slot.get("time")): slot for slot in slots if slot.get("time")}
    normalized = []
    for slot_time in SLOT_TIMES:
        source = by_time.get(slot_time) or {}
        normalized.append(
            {
                "time": slot_time,
                "metrics": normalize_metrics(source.get("metrics") or source),
                "target_urls": extract_urls(json.dumps(source, ensure_ascii=False)),
            }
        )
    return normalized


def normalize_metrics(metrics: Dict[str, Any]) -> Dict[str, int]:
    aliases = {
        "点赞量": "likes", "点赞": "likes", "like": "likes", "likes": "likes",
        "收藏量": "bookmarks", "收藏": "bookmarks", "bookmarks": "bookmarks",
        "转帖量": "retweets", "转帖": "retweets", "retweets": "retweets",
        "评论量": "replies", "评论": "replies", "replies": "replies",
        "关注量": "follows", "关注": "follows", "follows": "follows",
        "发帖量": "posts", "发帖": "posts", "posts": "posts",
        "手动搜索量": "manual_searches", "手动搜索": "manual_searches", "manual_searches": "manual_searches",
    }
    result = {key: 0 for key in METRIC_KEYS}
    for key, value in metrics.items():
        mapped = aliases.get(str(key), str(key))
        if mapped in result:
            result[mapped] = max(0, to_int(value))
    return result


def farming_overrides(metrics: Dict[str, int]) -> Dict[str, Any]:
    return {
        "FARMING_CONFIG": {
            "max_likes": int(metrics.get("likes") or 0),
            "max_bookmarks": int(metrics.get("bookmarks") or 0),
            "max_retweets": int(metrics.get("retweets") or 0),
            "max_replies": int(metrics.get("replies") or 0),
            "max_follows": int(metrics.get("follows") or 0),
            "max_manual_searches": int(metrics.get("manual_searches") or 0),
        }
    }


def boost_overrides(metrics: Dict[str, int]) -> Dict[str, Any]:
    return {"TARGET_BOOST_CONFIG": {"max_replies": int(metrics.get("replies") or 0)}}


def has_interaction(metrics: Dict[str, int]) -> bool:
    return any(int(metrics.get(k) or 0) > 0 for k in ("likes", "bookmarks", "retweets", "replies", "follows", "manual_searches"))


def has_actionable_metrics(days: List[Dict[str, Any]]) -> bool:
    for day in days or []:
        for slot in day.get("slots") or []:
            metrics = normalize_metrics(slot.get("metrics") or {})
            if has_interaction(metrics) or int(metrics.get("posts") or 0) > 0 or (slot.get("target_urls") or []):
                return True
    return False


def ensure_day(days: List[Dict[str, Any]], day_index: int) -> Dict[str, Any]:
    while len(days) < day_index:
        days.append({"day_index": len(days) + 1, "slots": []})
    return days[day_index - 1]


def normalize_time(value: Any) -> str:
    match = re.search(r"([01]?\d|2[0-3])[:：]([0-5]\d)", str(value or ""))
    if not match:
        return "09:00"
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def to_int(value: Any) -> int:
    match = re.search(r"-?\d+", str(value or "0"))
    return int(match.group(0)) if match else 0


def extract_score(raw: str) -> int | None:
    match = re.search(r"(\d{1,3})\s*(?:分|/100|／100)", raw or "")
    if not match:
        return None
    return min(100, max(0, int(match.group(1))))


def extract_urls(text: str) -> List[str]:
    return re.findall(r"https?://[^\s，,|]+", text or "")


def to_ts(run_date: Any, hhmm: str, skip_expired: bool = False) -> int | None:
    if isinstance(run_date, str):
        run_date = date.fromisoformat(run_date)
    hour, minute = [int(part) for part in hhmm.split(":", 1)]
    dt = datetime.combine(run_date, datetime.min.time()).replace(hour=hour, minute=minute)
    if dt.timestamp() < time.time():
        if skip_expired:
            return None
        dt = dt + timedelta(days=1)
    return int(dt.timestamp())
