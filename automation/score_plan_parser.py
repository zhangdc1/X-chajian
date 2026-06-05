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


RANDOM_SLOT_COUNT = 5
RANDOM_START_MINUTE = 9 * 60
RANDOM_END_MINUTE = 22 * 60 + 30
RANDOM_MIN_GAP_MINUTES = 90
METRIC_KEYS = {
    "likes": "点赞量",
    "bookmarks": "收藏量",
    "retweets": "转帖量",
    "replies": "评论量",
    "follows": "关注量",
    "posts": "发帖量",
    "manual_searches": "手动搜索量",
}


def parse_score_plan(raw: str, period: str = "weekly", seed_extra: str = "") -> Dict[str, Any]:
    raw = raw or ""
    parsed = parse_markdown_tables(raw)
    if not parsed:
        parsed = parse_plain_metric_tables(raw)
    parser = "table"
    if not parsed:
        parsed = parse_with_model(raw)
        parser = "model" if parsed else "rules"
    if not parsed:
        parsed = parse_by_rules(raw, period, seed_extra=seed_extra)
        parser = "rules"
    elif not has_actionable_metrics(parsed):
        parsed = parse_by_rules(raw, period, seed_extra=seed_extra)
        parser = "rules"
    return {
        "type": "score_plan",
        "parser": parser,
        "period": period,
        "score": extract_score(raw),
        "days": normalize_days(parsed, period, seed_extra=seed_extra),
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
    fallback_seed = str(account_id or group_id or plan.get("id") or "")
    fallback_days = normalize_days(
        parse_by_rules(str(plan.get("raw_excerpt") or ""), fallback_period, seed_extra=fallback_seed),
        fallback_period,
        seed_extra=fallback_seed,
    )
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
        '{"days":[{"day_index":1,"slots":[{"time":"13:25","metrics":{"likes":1,"bookmarks":1,'
        '"retweets":1,"replies":1,"follows":1,"posts":0,"manual_searches":1},"target_urls":[]}]}]}。'
        "保留原文里的 HH:MM 时间；缺失数值填 0；如果时间缺失或不合法，time 填空字符串。\n\n"
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


def parse_by_rules(raw: str, period: str, seed_extra: str = "") -> List[Dict[str, Any]]:
    days_count = 1 if period == "daily" else 7
    rng = random.Random(seed_int(period, raw[:1000], seed_extra))
    days = []
    metric_ranges = {
        "likes": (15, 35),
        "bookmarks": (3, 12),
        "retweets": (2, 8),
        "replies": (2, 12),
        "follows": (2, 5),
        "manual_searches": (1, 2),
    }
    for day_index in range(1, days_count + 1):
        slots = []
        slot_times = random_slot_times(f"{period}|{seed_extra}|day={day_index}|{raw[:300]}")
        progress = 0 if days_count <= 1 else (day_index - 1) / max(1, days_count - 1)
        day_trend = 0.90 + progress * 0.16 + rng.uniform(-0.04, 0.04)
        post_target = rng.choice([1, 1, 2, 2, 3])
        post_indices = set(rng.sample(range(RANDOM_SLOT_COUNT), k=min(3, post_target)))
        follows_remaining = 20
        for index, slot_time in enumerate(slot_times):
            slots_left = RANDOM_SLOT_COUNT - index
            min_for_rest = 2 * (slots_left - 1)
            follow_low, follow_high = metric_ranges["follows"]
            follow_max = min(follow_high, follows_remaining - min_for_rest)
            follows = rng.randint(follow_low, max(follow_low, follow_max))
            follows_remaining -= follows
            slot_bias = (index - 2) * 0.025
            slots.append(
                {
                    "time": slot_time,
                    "metrics": {
                        "likes": vary_metric(rng, metric_ranges["likes"], day_trend + slot_bias),
                        "bookmarks": vary_metric(rng, metric_ranges["bookmarks"], day_trend + slot_bias * 0.8),
                        "retweets": vary_metric(rng, metric_ranges["retweets"], day_trend + slot_bias * 0.6),
                        "replies": vary_metric(rng, metric_ranges["replies"], day_trend + slot_bias * 0.7),
                        "follows": follows,
                        "posts": 1 if index in post_indices else 0,
                        "manual_searches": rng.randint(*metric_ranges["manual_searches"]),
                    },
                }
            )
        days.append({"day_index": day_index, "slots": slots})
    return days


def vary_metric(rng: random.Random, bounds: tuple[int, int], day_factor: float) -> int:
    low, high = bounds
    base = rng.randint(low, high)
    spread = max(1, high - low)
    trend_adjust = int(round((day_factor - 1.0) * spread))
    jitter = rng.randint(-2, 2)
    return max(low, min(high, base + trend_adjust + jitter))


def normalize_days(days: List[Dict[str, Any]], period: str, seed_extra: str = "") -> List[Dict[str, Any]]:
    count = 1 if period == "daily" else 30 if period == "monthly" else 7
    result = []
    today = date.today()
    for idx in range(count):
        source = days[idx] if idx < len(days) else {"slots": []}
        slots = normalize_slots(source.get("slots") or [], seed_extra=f"{seed_extra}|day={idx + 1}")
        result.append({"day_index": idx + 1, "date": (today + timedelta(days=idx)).isoformat(), "slots": slots})
    return result


def normalize_slots(slots: List[Dict[str, Any]], seed_extra: str = "") -> List[Dict[str, Any]]:
    normalized = []
    seen = set()
    for slot in slots:
        slot_time = normalize_time(slot.get("time"))
        if not slot_time or slot_time in seen:
            continue
        seen.add(slot_time)
        normalized.append(
            {
                "time": slot_time,
                "metrics": normalize_metrics(slot.get("metrics") or slot),
                "target_urls": extract_urls(json.dumps(slot, ensure_ascii=False)),
            }
        )
    normalized.sort(key=lambda item: item["time"])
    if len(normalized) >= RANDOM_SLOT_COUNT:
        return normalized[:RANDOM_SLOT_COUNT]
    for slot_time in random_slot_times(f"normalize|{seed_extra}"):
        if slot_time in seen:
            continue
        seen.add(slot_time)
        normalized.append(
            {
                "time": slot_time,
                "metrics": {key: 0 for key in METRIC_KEYS},
                "target_urls": [],
            }
        )
        if len(normalized) >= RANDOM_SLOT_COUNT:
            break
    normalized.sort(key=lambda item: item["time"])
    return normalized


def seed_int(*parts: Any) -> int:
    text = "|".join(str(part) for part in parts)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


def random_slot_times(seed_material: str) -> List[str]:
    rng = random.Random(seed_int(seed_material))
    candidates = list(range(RANDOM_START_MINUTE, RANDOM_END_MINUTE + 1, 5))
    for _ in range(1000):
        picks = sorted(rng.sample(candidates, RANDOM_SLOT_COUNT))
        if all((b - a) >= RANDOM_MIN_GAP_MINUTES for a, b in zip(picks, picks[1:])):
            return [minute_to_hhmm(item) for item in picks]
    span = RANDOM_END_MINUTE - RANDOM_START_MINUTE
    picks = []
    for index in range(RANDOM_SLOT_COUNT):
        base = RANDOM_START_MINUTE + round(index * span / max(1, RANDOM_SLOT_COUNT - 1))
        jitter = rng.randint(-20, 20)
        minute = min(RANDOM_END_MINUTE, max(RANDOM_START_MINUTE, base + jitter))
        minute = int(round(minute / 5) * 5)
        picks.append(minute)
    picks = sorted(dict.fromkeys(picks))
    while len(picks) < RANDOM_SLOT_COUNT:
        for candidate in candidates:
            if candidate not in picks and all(abs(candidate - existing) >= 60 for existing in picks):
                picks.append(candidate)
                break
    return [minute_to_hhmm(item) for item in sorted(picks[:RANDOM_SLOT_COUNT])]


def minute_to_hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


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
        return ""
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
