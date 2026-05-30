import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from automation.plan_parser import build_tasks_from_plan
from automation.score_plan_parser import build_tasks_from_score_plan


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    target_node_id TEXT,
    leased_by TEXT,
    lease_until INTEGER,
    result_json TEXT,
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS worker_nodes (
    node_id TEXT PRIMARY KEY,
    label TEXT,
    last_seen INTEGER NOT NULL,
    status TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS accounts (
    profile_id TEXT PRIMARY KEY,
    profile_name TEXT,
    group_id TEXT,
    node_id TEXT NOT NULL,
    x_username TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    meta_json TEXT NOT NULL DEFAULT '{}',
    last_seen INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS account_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    group_id TEXT,
    period TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    source_job_id INTEGER,
    grok_raw_response TEXT,
    parsed_plan_json TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS comment_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT,
    tweet_url TEXT,
    tweet_text TEXT,
    draft_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'drafted',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER,
    account_id TEXT NOT NULL,
    group_id TEXT,
    node_id TEXT,
    job_type TEXT NOT NULL,
    run_at INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    job_id INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES account_plans(id)
);

CREATE TABLE IF NOT EXISTS group_aliases (
    alias TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_sync_groups (
    node_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(node_id, group_id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_text TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


DEFAULT_SCORE_PROMPT = """请帮我制定一份社交媒体账号权重提升计划，要求如下：

一、计划目标
一周内将账号权重从当前分数提升到60分（满分100分），今天是第一天。

二、输出格式
只输出表格，不要任何文字说明。每天单独一个表格。

三、每天表格结构
- 第一列：时间（固定5个时间点：9:00、12:00、15:00、18:00、21:00）
- 其余列：点赞量、收藏量、转帖量、评论量、关注量、发帖量、手动搜索量
- 每个单元格必须是具体的整数，禁止出现“全天分散”、“各时段执行”、“区间合并”（如08:00-09:00）等写法

四、重要定义与数值范围（请严格遵守）
1. 手动搜索量：指“主动输入行业关键词并点击搜索”的次数。每次独立搜索（输入关键词+按确认）计为1。不是浏览结果的点赞或停留时间。
   - 每个时间点手动搜索量建议为 1 或 2，全天不超过 10。
   - 禁止出现大于 5 的数值。

2. 其他指标的合理范围（每时间点）：
   - 点赞量：15~35
   - 收藏量：3~12
   - 转帖量：2~8
   - 评论量：2~12
   - 关注量：2~5
   - 发帖量：0 或 1

3. 每天总关注量不超过 20，总发帖量不超过 3。

五、计划周期
- 第一天：从今天开始，时间点 9:00、12:00、15:00、18:00、21:00 执行。
- 后面六天：格式与第一天完全相同，数值可逐日微增，但不得超出上述范围。

请直接输出 7 天的表格（第1天到第7天），不要额外解释。表格中手动搜索量必须为 1 或 2。"""


def now_ts() -> int:
    return int(time.time())


class Storage:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            self._ensure_column(conn, "account_plans", "source_job_id", "INTEGER")
            self._ensure_column(conn, "account_plans", "plan_type", "TEXT DEFAULT 'grok_plan'")
            self._ensure_column(conn, "account_plans", "score", "INTEGER")
            self._ensure_column(conn, "account_plans", "is_current", "INTEGER DEFAULT 1")
            self._ensure_column(conn, "scheduled_tasks", "last_error", "TEXT")
            self._ensure_column(conn, "group_aliases", "status", "TEXT DEFAULT 'active'")
            conn.execute("UPDATE group_aliases SET status = 'active' WHERE status IS NULL OR status = ''")
            self._ensure_default_score_prompt(conn)
            self.cancel_legacy_grok_score_jobs(conn)

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row[1] == column for row in rows):
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create_job(
        self,
        job_type: str,
        payload: Dict[str, Any],
        target_node_id: Optional[str] = None,
    ) -> int:
        ts = now_ts()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs (
                    job_type, payload_json, target_node_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (job_type, json.dumps(payload, ensure_ascii=False), target_node_id, ts, ts),
            )
            return int(cur.lastrowid)

    def cancel_legacy_grok_score_jobs(self, conn: sqlite3.Connection) -> Dict[str, int]:
        ts = now_ts()
        markers = (
            "%请根据当前 X 账号状态%",
            "%生成 weekly 账号评分%",
            "%生成 monthly 账号评分%",
            "%生成 daily 账号评分%",
        )
        where = " OR ".join("payload_json LIKE ?" for _ in markers)
        rows = conn.execute(
            f"""
            SELECT id, status FROM jobs
            WHERE job_type = 'generate_grok_plan'
              AND status IN ('queued', 'leased')
              AND ({where})
            """,
            markers,
        ).fetchall()
        queued_ids = [int(row["id"]) for row in rows if row["status"] == "queued"]
        leased_ids = [int(row["id"]) for row in rows if row["status"] == "leased"]
        if queued_ids:
            placeholders = ",".join("?" for _ in queued_ids)
            conn.execute(
                f"""
                UPDATE jobs
                SET status = 'cancelled',
                    error = '旧Grok评分入口已停用，请重新执行账号评分',
                    updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [ts, *queued_ids],
            )
        if leased_ids:
            placeholders = ",".join("?" for _ in leased_ids)
            conn.execute(
                f"""
                UPDATE jobs
                SET status = 'cancel_requested',
                    error = '旧Grok评分入口已停用，请重新执行账号评分',
                    updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [ts, *leased_ids],
            )
        for job_id in queued_ids:
            conn.execute(
                """
                INSERT INTO job_runs (job_id, node_id, status, message, created_at)
                VALUES (?, '', 'cancelled', '旧Grok评分入口已停用，请重新执行账号评分', ?)
                """,
                (job_id, ts),
            )
        for job_id in leased_ids:
            conn.execute(
                """
                INSERT INTO job_runs (job_id, node_id, status, message, created_at)
                VALUES (?, '', 'cancel_requested', '旧Grok评分入口已停用，请重新执行账号评分', ?)
                """,
                (job_id, ts),
            )
        if queued_ids or leased_ids:
            job_ids = [*queued_ids, *leased_ids]
            placeholders = ",".join("?" for _ in job_ids)
            conn.execute(
                f"""
                UPDATE account_plans
                SET status = 'cancelled_by_new_plan',
                    is_current = 0,
                    updated_at = ?
                WHERE source_job_id IN ({placeholders})
                """,
                [ts, *job_ids],
            )
        return {"queued_cancelled": len(queued_ids), "leased_requested": len(leased_ids)}

    def _ensure_default_score_prompt(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT value_text FROM app_settings WHERE key = 'score_prompt'").fetchone()
        if row:
            value_text = str(row["value_text"] or "")
            correct_prompt_path = Path("账号评分提示词.txt")
            legacy_default_markers = (
                "请根据当前 X 账号状态",
                "生成 weekly 账号评分",
                "生成 {period} 账号评分",
                "生成 weekly 账号评分、内容方向",
            )
            if correct_prompt_path.exists() and (
                "璇峰府" in value_text
                or "鎻愮ず" in value_text
                or any(marker in value_text for marker in legacy_default_markers)
            ):
                conn.execute(
                    "UPDATE app_settings SET value_text = ?, updated_at = ? WHERE key = 'score_prompt'",
                    (correct_prompt_path.read_text(encoding="utf-8"), now_ts()),
                )
            return
        correct_prompt_path = Path("账号评分提示词.txt")
        if correct_prompt_path.exists():
            value = correct_prompt_path.read_text(encoding="utf-8")
            conn.execute(
                "INSERT INTO app_settings (key, value_text, updated_at) VALUES ('score_prompt', ?, ?)",
                (value, now_ts()),
            )
            return
        prompt_path = Path("账号评分提示词.txt")
        if prompt_path.exists():
            value = prompt_path.read_text(encoding="utf-8")
        else:
            value = DEFAULT_SCORE_PROMPT
        conn.execute(
            "INSERT INTO app_settings (key, value_text, updated_at) VALUES ('score_prompt', ?, ?)",
            (value, now_ts()),
        )

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value_text FROM app_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value_text"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        ts = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value_text, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_text = excluded.value_text,
                    updated_at = excluded.updated_at
                """,
                (key, value, ts),
            )

    def set_group_alias(self, alias: str, group_id: str) -> None:
        with self.connect() as conn:
            self._set_group_alias(conn, alias, group_id)

    def delete_group_alias(self, alias: str) -> Dict[str, int]:
        ts = now_ts()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE group_aliases
                SET status = 'deleted', updated_at = ?
                WHERE alias = ? AND status <> 'deleted'
                """,
                (ts, alias.strip()),
            )
        return {"aliases": int(cur.rowcount or 0)}

    def _set_group_alias(self, conn: sqlite3.Connection, alias: str, group_id: str) -> None:
        alias = (alias or "").strip()
        group_id = (group_id or "").strip()
        if not alias or not group_id:
            return
        ts = now_ts()
        conn.execute(
            """
            INSERT INTO group_aliases (alias, group_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                group_id = excluded.group_id,
                status = 'active',
                updated_at = excluded.updated_at
            """,
            (alias, group_id, ts, ts),
        )

    def add_worker_sync_group(self, node_id: str, group_id: str) -> Dict[str, int]:
        node_id = (node_id or "").strip()
        group_id = (group_id or "").strip()
        if not node_id or not group_id:
            raise ValueError("missing node_id or group_id")
        ts = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_sync_groups (node_id, group_id, status, created_at, updated_at)
                VALUES (?, ?, 'active', ?, ?)
                ON CONFLICT(node_id, group_id) DO UPDATE SET
                    status = 'active',
                    updated_at = excluded.updated_at
                """,
                (node_id, group_id, ts, ts),
            )
        return {"sync_groups": 1}

    def remove_worker_sync_group(self, node_id: str, group_id: str) -> Dict[str, int]:
        ts = now_ts()
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE worker_sync_groups
                SET status = 'deleted', updated_at = ?
                WHERE node_id = ? AND group_id = ? AND status <> 'deleted'
                """,
                (ts, (node_id or "").strip(), (group_id or "").strip()),
            )
        return {"sync_groups": int(cur.rowcount or 0)}

    def list_worker_sync_groups(self, node_id: Optional[str] = None) -> list[Dict[str, Any]]:
        sql = "SELECT * FROM worker_sync_groups WHERE status = 'active'"
        params: list[Any] = []
        if node_id:
            sql += " AND node_id = ?"
            params.append(node_id)
        sql += " ORDER BY node_id, group_id"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def resolve_group_id(self, identifier: Optional[str]) -> Optional[str]:
        identifier = (identifier or "").strip()
        if not identifier:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT group_id FROM group_aliases WHERE alias = ? AND status = 'active'",
                (identifier,),
            ).fetchone()
            if row:
                return row["group_id"]
            row = conn.execute("SELECT 1 FROM accounts WHERE group_id = ? LIMIT 1", (identifier,)).fetchone()
            if row:
                return identifier
        return identifier

    def list_groups(self, limit: int = 100) -> list[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH account_groups AS (
                    SELECT
                        group_id,
                        SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS account_count,
                        SUM(CASE WHEN status <> 'active' THEN 1 ELSE 0 END) AS inactive_count,
                        GROUP_CONCAT(DISTINCT node_id) AS node_ids,
                        MAX(last_seen) AS last_seen,
                        MAX(updated_at) AS updated_at
                    FROM accounts
                    WHERE COALESCE(group_id, '') <> ''
                    GROUP BY group_id
                ),
                alias_groups AS (
                    SELECT
                        group_id,
                        GROUP_CONCAT(alias, ', ') AS alias,
                        MAX(updated_at) AS updated_at
                    FROM group_aliases
                    WHERE COALESCE(group_id, '') <> ''
                      AND status = 'active'
                    GROUP BY group_id
                ),
                sync_groups AS (
                    SELECT
                        group_id,
                        GROUP_CONCAT(DISTINCT node_id) AS sync_node_ids,
                        MAX(updated_at) AS sync_updated_at
                    FROM worker_sync_groups
                    WHERE status = 'active'
                    GROUP BY group_id
                )
                SELECT
                    COALESCE(ag.group_id, sg.group_id) AS group_id,
                    COALESCE(alg.alias, '') AS alias,
                    COALESCE(ag.account_count, 0) AS account_count,
                    COALESCE(ag.inactive_count, 0) AS inactive_count,
                    COALESCE(ag.node_ids, '') AS node_ids,
                    COALESCE(ag.last_seen, 0) AS last_seen,
                    COALESCE(sg.sync_node_ids, '') AS sync_node_ids,
                    MAX(COALESCE(ag.updated_at, 0), COALESCE(alg.updated_at, 0), COALESCE(sg.sync_updated_at, 0)) AS updated_at
                FROM account_groups ag
                LEFT JOIN alias_groups alg ON alg.group_id = ag.group_id
                LEFT JOIN sync_groups sg ON sg.group_id = ag.group_id
                UNION ALL
                SELECT
                    alg.group_id AS group_id,
                    COALESCE(alg.alias, '') AS alias,
                    0 AS account_count,
                    0 AS inactive_count,
                    '' AS node_ids,
                    0 AS last_seen,
                    COALESCE(sg.sync_node_ids, '') AS sync_node_ids,
                    MAX(COALESCE(alg.updated_at, 0), COALESCE(sg.sync_updated_at, 0)) AS updated_at
                FROM alias_groups alg
                LEFT JOIN account_groups ag ON ag.group_id = alg.group_id
                LEFT JOIN sync_groups sg ON sg.group_id = alg.group_id
                WHERE ag.group_id IS NULL
                UNION ALL
                SELECT
                    sg.group_id AS group_id,
                    '' AS alias,
                    0 AS account_count,
                    0 AS inactive_count,
                    '' AS node_ids,
                    0 AS last_seen,
                    COALESCE(sg.sync_node_ids, '') AS sync_node_ids,
                    sg.sync_updated_at AS updated_at
                FROM sync_groups sg
                LEFT JOIN account_groups ag ON ag.group_id = sg.group_id
                LEFT JOIN alias_groups alg ON alg.group_id = sg.group_id
                WHERE ag.group_id IS NULL AND alg.group_id IS NULL
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def lease_next_job(self, node_id: str, lease_seconds: int = 300) -> Optional[Dict[str, Any]]:
        ts = now_ts()
        lease_until = ts + lease_seconds
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                  AND (target_node_id IS NULL OR target_node_id = ?)
                ORDER BY id ASC
                LIMIT 1
                """,
                (node_id,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                """
                UPDATE jobs
                SET status = 'leased', leased_by = ?, lease_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (node_id, lease_until, ts, row["id"]),
            )
            conn.execute(
                """
                INSERT INTO job_runs (job_id, node_id, status, message, created_at)
                VALUES (?, ?, 'leased', 'job leased by worker', ?)
                """,
                (row["id"], node_id, ts),
            )
            conn.commit()
            return self.get_job(int(row["id"]))

    def complete_job(self, job_id: int, node_id: str, result: Dict[str, Any]) -> None:
        self._finish_job(job_id, node_id, "completed", result, None)
        self._maybe_store_grok_plan(job_id, result)
        self._maybe_update_scheduled_task(job_id, "completed", "")

    def fail_job(self, job_id: int, node_id: str, error: str) -> None:
        current = self.get_job(job_id)
        if current and current.get("status") == "completed":
            self.add_job_run(
                job_id,
                node_id,
                "ignored_late_fail",
                f"忽略迟到失败上报，任务已完成：{error}",
            )
            return
        self._finish_job(job_id, node_id, "failed", None, error)
        self._maybe_update_scheduled_task(job_id, "failed", error)

    def cancel_job(self, job_id: int, reason: str = "cancelled_by_user") -> Dict[str, int]:
        ts = now_ts()
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise ValueError(f"job not found: {job_id}")
            status = str(row["status"])
            if status == "queued":
                new_status = "cancelled"
            elif status == "leased":
                new_status = "cancel_requested"
            else:
                return {"jobs": 0}
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (new_status, reason, ts, job_id),
            )
            conn.execute(
                """
                INSERT INTO job_runs (job_id, node_id, status, message, created_at)
                VALUES (?, '', ?, ?, ?)
                """,
                (job_id, new_status, reason, ts),
            )
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = CASE WHEN ? = 'cancelled' THEN 'cancelled_by_user' ELSE status END,
                    last_error = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (new_status, reason, ts, job_id),
            )
        return {"jobs": 1}

    def cancel_jobs_for_plan(self, plan_id: int, reason: str = "cancelled_by_plan") -> Dict[str, int]:
        ts = now_ts()
        with self.connect() as conn:
            return self._cancel_jobs_for_plan(conn, plan_id, reason, ts)

    def _cancel_jobs_for_plan(
        self,
        conn: sqlite3.Connection,
        plan_id: int,
        reason: str,
        ts: Optional[int] = None,
    ) -> Dict[str, int]:
        ts = ts or now_ts()
        rows = conn.execute(
            """
            SELECT id, status FROM jobs
            WHERE status IN ('queued', 'leased')
              AND (
                id IN (SELECT job_id FROM scheduled_tasks WHERE plan_id = ? AND job_id IS NOT NULL)
                OR json_extract(payload_json, '$.plan_id') = ?
              )
            """,
            (plan_id, plan_id),
        ).fetchall()
        queued_ids = [int(row["id"]) for row in rows if row["status"] == "queued"]
        leased_ids = [int(row["id"]) for row in rows if row["status"] == "leased"]
        if queued_ids:
            placeholders = ",".join("?" for _ in queued_ids)
            conn.execute(
                f"UPDATE jobs SET status = 'cancelled', error = ?, updated_at = ? WHERE id IN ({placeholders})",
                [reason, ts, *queued_ids],
            )
        if leased_ids:
            placeholders = ",".join("?" for _ in leased_ids)
            conn.execute(
                f"UPDATE jobs SET status = 'cancel_requested', error = ?, updated_at = ? WHERE id IN ({placeholders})",
                [reason, ts, *leased_ids],
            )
        for job_id in queued_ids:
            conn.execute(
                """
                INSERT INTO job_runs (job_id, node_id, status, message, created_at)
                VALUES (?, '', 'cancelled', ?, ?)
                """,
                (job_id, reason, ts),
            )
        for job_id in leased_ids:
            conn.execute(
                """
                INSERT INTO job_runs (job_id, node_id, status, message, created_at)
                VALUES (?, '', 'cancel_requested', ?, ?)
                """,
                (job_id, reason, ts),
            )
        conn.execute(
            """
            UPDATE scheduled_tasks
            SET status = 'cancelled_by_user',
                last_error = ?,
                updated_at = ?
            WHERE plan_id = ? AND status = 'dispatched'
            """,
            (reason, ts, plan_id),
        )
        return {"queued_cancelled": len(queued_ids), "leased_requested": len(leased_ids)}

    def _cancel_jobs_for_plan_ids(
        self,
        conn: sqlite3.Connection,
        plan_ids: list[int],
        reason: str,
        ts: Optional[int] = None,
    ) -> int:
        total = 0
        for plan_id in plan_ids:
            result = self._cancel_jobs_for_plan(conn, plan_id, reason, ts)
            total += int(result.get("queued_cancelled", 0)) + int(result.get("leased_requested", 0))
        return total

    def add_job_run(self, job_id: int, node_id: str, status: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO job_runs (job_id, node_id, status, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, node_id, status, message, now_ts()),
            )

    def _maybe_update_scheduled_task(self, job_id: int, status: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET status = ?, last_error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, error, now_ts(), job_id),
            )

    def set_score_plan_status(self, plan_id: int, action: str, late_grace_seconds: int = 3600) -> Dict[str, int]:
        ts = now_ts()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM account_plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise ValueError(f"score plan not found: {plan_id}")
            if row["plan_type"] != "score_plan":
                raise ValueError(f"not a score plan: {plan_id}")

            if action == "pause":
                cur = conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'paused', updated_at = ?
                    WHERE plan_id = ? AND status = 'scheduled'
                    """,
                    (ts, plan_id),
                )
                conn.execute(
                    "UPDATE account_plans SET status = 'paused', updated_at = ? WHERE id = ?",
                    (ts, plan_id),
                )
                cancelled = self._cancel_jobs_for_plan(conn, plan_id, "paused_by_user", ts)
                return {"plans": 1, "tasks": int(cur.rowcount or 0), **cancelled}

            if action == "resume":
                cutoff = ts - int(late_grace_seconds)
                expired = conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'expired_missed',
                        last_error = 'missed schedule while plan was paused',
                        updated_at = ?
                    WHERE plan_id = ? AND status = 'paused' AND run_at < ?
                    """,
                    (ts, plan_id, cutoff),
                )
                resumed = conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'scheduled', updated_at = ?
                    WHERE plan_id = ? AND status = 'paused'
                    """,
                    (ts, plan_id),
                )
                conn.execute(
                    "UPDATE account_plans SET status = 'auto_scheduled', updated_at = ? WHERE id = ?",
                    (ts, plan_id),
                )
                return {
                    "plans": 1,
                    "tasks": int(resumed.rowcount or 0),
                    "expired": int(expired.rowcount or 0),
                }

            if action == "delete":
                cur = conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'cancelled_by_user',
                        last_error = 'cancelled by user',
                        updated_at = ?
                    WHERE plan_id = ?
                      AND status IN ('scheduled', 'paused', 'dispatched')
                    """,
                    (ts, plan_id),
                )
                job_rows = conn.execute(
                    """
                    SELECT job_id FROM scheduled_tasks
                    WHERE plan_id = ? AND job_id IS NOT NULL
                    """,
                    (plan_id,),
                ).fetchall()
                job_ids = [int(item["job_id"]) for item in job_rows]
                if job_ids:
                    placeholders = ",".join("?" for _ in job_ids)
                    conn.execute(
                        f"""
                        UPDATE jobs
                        SET status = 'cancelled',
                            error = 'cancelled_by_user',
                            updated_at = ?
                        WHERE id IN ({placeholders}) AND status = 'queued'
                        """,
                        [ts, *job_ids],
                    )
                conn.execute(
                    """
                    UPDATE account_plans
                    SET status = 'deleted', is_current = 0, updated_at = ?
                    WHERE id = ?
                    """,
                    (ts, plan_id),
                )
                cancelled = self._cancel_jobs_for_plan(conn, plan_id, "cancelled_by_user", ts)
                return {"plans": 1, "tasks": int(cur.rowcount or 0), **cancelled}

            if action == "cancel-all":
                cur = conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'cancelled_by_user',
                        last_error = 'cancelled by user',
                        updated_at = ?
                    WHERE plan_id = ?
                      AND status IN ('scheduled', 'paused', 'dispatched')
                    """,
                    (ts, plan_id),
                )
                cancelled = self._cancel_jobs_for_plan(conn, plan_id, "cancelled_by_user", ts)
                return {"plans": 1, "tasks": int(cur.rowcount or 0), **cancelled}

        raise ValueError(f"unsupported score plan action: {action}")

    def _finish_job(
        self,
        job_id: int,
        node_id: str,
        status: str,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        ts = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, result_json = ?, error = ?, updated_at = ?
                WHERE id = ? AND leased_by = ?
                """,
                (
                    status,
                    json.dumps(result or {}, ensure_ascii=False),
                    error,
                    ts,
                    job_id,
                    node_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO job_runs (job_id, node_id, status, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, node_id, status, error or "job completed", ts),
            )

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        data["result"] = json.loads(data.pop("result_json") or "{}")
        return data

    def list_jobs(
        self,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        node_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[Dict[str, Any]]:
        sql = "SELECT * FROM jobs WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if job_type:
            sql += " AND job_type = ?"
            params.append(job_type)
        if node_id:
            sql += " AND (target_node_id = ? OR leased_by = ?)"
            params.extend([node_id, node_id])
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            item["result"] = json.loads(item.pop("result_json") or "{}")
            result.append(item)
        return result

    def list_job_runs(self, job_id: Optional[int] = None, limit: int = 100) -> list[Dict[str, Any]]:
        sql = "SELECT * FROM job_runs WHERE 1=1"
        params: list[Any] = []
        if job_id is not None:
            sql += " AND job_id = ?"
            params.append(job_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def heartbeat_worker(self, node_id: str, label: str, status: str, meta: Dict[str, Any]) -> None:
        ts = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_nodes (node_id, label, last_seen, status, meta_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    label = excluded.label,
                    last_seen = excluded.last_seen,
                    status = excluded.status,
                    meta_json = excluded.meta_json
                """,
                (node_id, label, ts, status, json.dumps(meta, ensure_ascii=False)),
            )

    def list_workers(self, limit: int = 100) -> list[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM worker_nodes
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["meta"] = json.loads(item.pop("meta_json") or "{}")
            result.append(item)
        return result

    def upsert_accounts(
        self,
        node_id: str,
        accounts: list[Dict[str, Any]],
        synced_group_ids: Optional[list[str]] = None,
    ) -> Dict[str, int]:
        ts = now_ts()
        seen_profile_ids: set[str] = set()
        group_ids = {str(item).strip() for item in (synced_group_ids or []) if str(item).strip()}
        with self.connect() as conn:
            for account in accounts:
                profile_id = str(account.get("profile_id") or account.get("id") or "").strip()
                if not profile_id:
                    continue
                seen_profile_ids.add(profile_id)
                group_id = str(account.get("group_id") or account.get("groupId") or "").strip()
                if group_id:
                    group_ids.add(group_id)
                conn.execute(
                    """
                    INSERT INTO accounts (
                        profile_id, profile_name, group_id, node_id, x_username,
                        status, meta_json, last_seen, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    ON CONFLICT(profile_id) DO UPDATE SET
                        profile_name = excluded.profile_name,
                        group_id = excluded.group_id,
                        node_id = excluded.node_id,
                        x_username = excluded.x_username,
                        status = 'active',
                        meta_json = excluded.meta_json,
                        last_seen = excluded.last_seen,
                        updated_at = excluded.updated_at
                    """,
                    (
                        profile_id,
                        account.get("profile_name") or account.get("name") or "",
                        group_id,
                        node_id,
                        account.get("x_username") or account.get("username") or "",
                        json.dumps(account.get("meta", account), ensure_ascii=False),
                        ts,
                        ts,
                    ),
                )
                group_name = (
                    account.get("group_name")
                    or account.get("groupName")
                    or (account.get("meta") or {}).get("groupName")
                    or (account.get("meta") or {}).get("group_name")
                )
                if group_name:
                    self._set_group_alias(
                        conn,
                        str(group_name),
                        group_id,
                    )
            deactivated = 0
            cancelled_jobs = 0
            cancelled_tasks = 0
            cancelled_plans = 0
            if group_ids:
                placeholders = ",".join("?" for _ in group_ids)
                params: list[Any] = [node_id, *group_ids]
                keep_clause = ""
                if seen_profile_ids:
                    keep_placeholders = ",".join("?" for _ in seen_profile_ids)
                    keep_clause = f" AND profile_id NOT IN ({keep_placeholders})"
                    params.extend(sorted(seen_profile_ids))
                rows = conn.execute(
                    f"""
                    SELECT profile_id FROM accounts
                    WHERE node_id = ?
                      AND status = 'active'
                      AND group_id IN ({placeholders})
                      {keep_clause}
                    """,
                    params,
                ).fetchall()
                stale_ids = [str(row["profile_id"]) for row in rows]
                if stale_ids:
                    stale_placeholders = ",".join("?" for _ in stale_ids)
                    conn.execute(
                        f"""
                        UPDATE accounts
                        SET status = 'inactive', updated_at = ?
                        WHERE profile_id IN ({stale_placeholders})
                        """,
                        [ts, *stale_ids],
                    )
                    deactivated = len(stale_ids)
                    plan_rows = conn.execute(
                        f"""
                        SELECT id FROM account_plans
                        WHERE account_id IN ({stale_placeholders})
                          AND plan_type = 'score_plan'
                          AND COALESCE(is_current, 1) = 1
                          AND status NOT IN ('deleted', 'cancelled_by_new_plan')
                        """,
                        stale_ids,
                    ).fetchall()
                    plan_ids = [int(row["id"]) for row in plan_rows]
                    if plan_ids:
                        plan_placeholders = ",".join("?" for _ in plan_ids)
                        conn.execute(
                            f"""
                            UPDATE account_plans
                            SET status = 'account_inactive', is_current = 0, updated_at = ?
                            WHERE id IN ({plan_placeholders})
                            """,
                            [ts, *plan_ids],
                        )
                        cancelled_plans = len(plan_ids)
                        task_cur = conn.execute(
                            f"""
                            UPDATE scheduled_tasks
                            SET status = 'cancelled_account_inactive',
                                last_error = 'account removed from synced group',
                                updated_at = ?
                            WHERE plan_id IN ({plan_placeholders})
                              AND status IN ('scheduled', 'paused', 'dispatched')
                            """,
                            [ts, *plan_ids],
                        )
                        cancelled_tasks = int(task_cur.rowcount or 0)
                        cancelled_jobs = self._cancel_jobs_for_plan_ids(
                            conn,
                            plan_ids,
                            "account removed from synced group",
                            ts,
                        )
        return {
            "count": len(seen_profile_ids),
            "deactivated": deactivated,
            "cancelled_plans": cancelled_plans,
            "cancelled_tasks": cancelled_tasks,
            "cancelled_jobs": cancelled_jobs,
        }

    def list_accounts(
        self,
        group_id: Optional[str] = None,
        node_id: Optional[str] = None,
        limit: int = 500,
        include_inactive: bool = False,
    ) -> list[Dict[str, Any]]:
        group_id = self.resolve_group_id(group_id)
        sql = "SELECT * FROM accounts WHERE 1=1"
        params: list[Any] = []
        if not include_inactive:
            sql += " AND status = 'active'"
        if group_id:
            sql += " AND group_id = ?"
            params.append(group_id)
        if node_id:
            sql += " AND node_id = ?"
            params.append(node_id)
        sql += " ORDER BY node_id, profile_name, profile_id LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["meta"] = json.loads(item.pop("meta_json") or "{}")
            result.append(item)
        return result

    def create_grok_plan_jobs(
        self,
        group_id: str,
        period: str,
        prompt_template: str,
        target_node_id: Optional[str] = None,
        limit: int = 500,
    ) -> list[int]:
        prompt_text = self.get_setting("score_prompt", "").strip()
        if not prompt_text:
            raise ValueError("missing central score prompt")
        return self.create_score_plan_jobs(
            group_id=group_id,
            period=period,
            prompt_text=prompt_text,
            target_node_id=target_node_id,
            limit=limit,
        ).get("job_ids", [])

    def create_score_plan_jobs(
        self,
        group_id: str,
        period: str,
        prompt_text: str,
        target_node_id: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        from automation.job_types import JOB_SCORE_GROK_PLAN

        resolved_group_id = self.resolve_group_id(group_id)
        accounts = self.list_accounts(group_id=resolved_group_id, node_id=target_node_id, limit=limit)
        job_ids = []
        cleaned_plans = 0
        cleaned_tasks = 0
        for account in accounts:
            cleanup = self.invalidate_old_score_plans(account["profile_id"])
            cleaned_plans += cleanup["plans"]
            cleaned_tasks += cleanup["tasks"]
            job_id = self.create_job(
                job_type=JOB_SCORE_GROK_PLAN,
                target_node_id=account["node_id"],
                payload={
                    "account_id": account["profile_id"],
                    "profile_id": account["profile_id"],
                    "profile_name": account.get("profile_name") or "",
                    "group_id": resolved_group_id or group_id,
                    "period": period,
                    "prompt": prompt_text,
                    "source": "score_prompt",
                },
            )
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO account_plans (
                        account_id, group_id, period, source_job_id,
                        grok_raw_response, parsed_plan_json, status,
                        plan_type, score, is_current, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, '', '{}', 'queued', 'score_plan', NULL, 1, ?, ?)
                    """,
                    (
                        account["profile_id"],
                        resolved_group_id or group_id or "",
                        period,
                        job_id,
                        now_ts(),
                        now_ts(),
                    ),
                )
            job_ids.append(job_id)
        return {"job_ids": job_ids, "cleaned_plans": cleaned_plans, "cleaned_tasks": cleaned_tasks}

    def invalidate_old_score_plans(self, account_id: str) -> Dict[str, int]:
        ts = now_ts()
        with self.connect() as conn:
            plan_rows = conn.execute(
                """
                SELECT id FROM account_plans
                WHERE account_id = ? AND plan_type = 'score_plan' AND COALESCE(is_current, 1) = 1
                """,
                (account_id,),
            ).fetchall()
            plan_ids = [int(row["id"]) for row in plan_rows]
            if not plan_ids:
                return {"plans": 0, "tasks": 0}
            placeholders = ",".join("?" for _ in plan_ids)
            task_count = conn.execute(
                f"""
                SELECT COUNT(*) AS c FROM scheduled_tasks
                WHERE plan_id IN ({placeholders})
                  AND status IN ('scheduled', 'paused', 'dispatched')
                """,
                plan_ids,
            ).fetchone()["c"]
            job_rows = conn.execute(
                f"""
                SELECT id, status FROM jobs
                WHERE status IN ('queued', 'leased')
                  AND (
                    id IN (
                      SELECT job_id FROM scheduled_tasks
                      WHERE plan_id IN ({placeholders}) AND job_id IS NOT NULL
                    )
                    OR json_extract(payload_json, '$.plan_id') IN ({placeholders})
                  )
                """,
                [*plan_ids, *plan_ids],
            ).fetchall()
            queued_job_ids = [int(row["id"]) for row in job_rows if row["status"] == "queued"]
            leased_job_ids = [int(row["id"]) for row in job_rows if row["status"] == "leased"]
            if queued_job_ids:
                job_placeholders = ",".join("?" for _ in queued_job_ids)
                conn.execute(
                    f"""
                    UPDATE jobs
                    SET status = 'cancelled', error = 'cancelled_by_new_plan', updated_at = ?
                    WHERE id IN ({job_placeholders})
                    """,
                    [ts, *queued_job_ids],
                )
            if leased_job_ids:
                job_placeholders = ",".join("?" for _ in leased_job_ids)
                conn.execute(
                    f"""
                    UPDATE jobs
                    SET status = 'cancel_requested', error = 'cancelled_by_new_plan', updated_at = ?
                    WHERE id IN ({job_placeholders})
                    """,
                    [ts, *leased_job_ids],
                )
            conn.execute(
                f"""
                UPDATE scheduled_tasks
                SET status = 'cancelled_by_new_plan', updated_at = ?
                WHERE plan_id IN ({placeholders})
                  AND status IN ('scheduled', 'paused', 'dispatched')
                """,
                [ts, *plan_ids],
            )
            conn.execute(
                f"""
                UPDATE account_plans
                SET status = 'superseded', is_current = 0, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [ts, *plan_ids],
            )
        return {"plans": len(plan_ids), "tasks": int(task_count or 0)}

    def create_legacy_mode_jobs(
        self,
        group_id: str,
        mode: int,
        payload: Dict[str, Any],
        target_node_id: Optional[str] = None,
        limit: int = 500,
    ) -> list[int]:
        from automation.job_types import JOB_LEGACY_MODE_RUN

        resolved_group_id = self.resolve_group_id(group_id)
        accounts = self.list_accounts(group_id=resolved_group_id, node_id=target_node_id, limit=limit)
        node_ids = sorted({account["node_id"] for account in accounts if account.get("node_id")})
        if target_node_id:
            node_ids = [target_node_id]
        job_ids = []
        for node_id in node_ids:
            job_payload = dict(payload)
            job_payload.update(
                {
                    "mode": mode,
                    "group_id": resolved_group_id or group_id,
                    "target_node_id": node_id,
                    "account_count": sum(1 for account in accounts if account.get("node_id") == node_id),
                }
            )
            job_id = self.create_job(
                job_type=JOB_LEGACY_MODE_RUN,
                payload=job_payload,
                target_node_id=node_id,
            )
            job_ids.append(job_id)
        return job_ids

    def list_plans(
        self,
        account_id: Optional[str] = None,
        group_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        current_only: bool = False,
    ) -> list[Dict[str, Any]]:
        sql = "SELECT * FROM account_plans WHERE 1=1"
        params: list[Any] = []
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        group_id = self.resolve_group_id(group_id)
        if group_id:
            sql += " AND group_id = ?"
            params.append(group_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if current_only:
            sql += " AND COALESCE(is_current, 1) = 1"
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["parsed_plan"] = json.loads(item.pop("parsed_plan_json") or "{}")
            item["task_summary"] = self.scheduled_task_summary(plan_id=int(item["id"]))
            result.append(item)
        return result

    def approve_plan(self, plan_id: int, tasks: list[Dict[str, Any]], status: str = "approved") -> int:
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ValueError(f"plan not found: {plan_id}")
        ts = now_ts()
        account_id = plan["account_id"]
        group_id = plan.get("group_id") or ""
        account = self.get_account(account_id)
        node_id = account.get("node_id") if account else None
        with self.connect() as conn:
            conn.execute(
                "UPDATE account_plans SET status = ?, updated_at = ? WHERE id = ?",
                (status, ts, plan_id),
            )
            inserted = 0
            for task in tasks:
                job_type = task.get("job_type")
                run_at = int(task.get("run_at") or ts)
                payload = task.get("payload") or {}
                if not job_type:
                    continue
                task_status = task.get("status") or "scheduled"
                conn.execute(
                    """
                    INSERT INTO scheduled_tasks (
                        plan_id, account_id, group_id, node_id, job_type,
                        run_at, payload_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        account_id,
                        group_id,
                        node_id,
                        job_type,
                        run_at,
                        json.dumps(payload, ensure_ascii=False),
                        task_status,
                        ts,
                        ts,
                    ),
                )
                inserted += 1
        return inserted

    def get_plan(self, plan_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM account_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["parsed_plan"] = json.loads(item.pop("parsed_plan_json") or "{}")
        item["task_summary"] = self.scheduled_task_summary(plan_id=int(item["id"]))
        return item

    def scheduled_task_summary(self, plan_id: Optional[int] = None, account_id: Optional[str] = None) -> Dict[str, int]:
        sql = "SELECT status, COUNT(*) AS count FROM scheduled_tasks WHERE 1=1"
        params: list[Any] = []
        if plan_id is not None:
            sql += " AND plan_id = ?"
            params.append(plan_id)
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        sql += " GROUP BY status"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        summary = {str(row["status"]): int(row["count"]) for row in rows}
        summary["total"] = sum(summary.values())
        return summary

    def get_account(self, profile_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE profile_id = ?", (profile_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["meta"] = json.loads(item.pop("meta_json") or "{}")
        return item

    def set_account_status(self, profile_id: str, status: str) -> Dict[str, int]:
        if status not in {"active", "inactive"}:
            raise ValueError(f"unsupported account status: {status}")
        ts = now_ts()
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE accounts SET status = ?, updated_at = ? WHERE profile_id = ?",
                (status, ts, profile_id),
            )
            if status == "inactive":
                cleanup = self._cancel_account_pending_work(conn, profile_id, "account disabled by user", ts)
            else:
                cleanup = {"plans": 0, "tasks": 0, "jobs": 0}
        return {"accounts": int(cur.rowcount or 0), **cleanup}

    def _cancel_account_pending_work(
        self,
        conn: sqlite3.Connection,
        profile_id: str,
        reason: str,
        ts: Optional[int] = None,
    ) -> Dict[str, int]:
        ts = ts or now_ts()
        plan_rows = conn.execute(
            """
            SELECT id FROM account_plans
            WHERE account_id = ?
              AND COALESCE(is_current, 1) = 1
              AND status NOT IN ('deleted', 'superseded', 'account_inactive')
            """,
            (profile_id,),
        ).fetchall()
        plan_ids = [int(row["id"]) for row in plan_rows]
        if not plan_ids:
            return {"plans": 0, "tasks": 0, "jobs": 0}
        placeholders = ",".join("?" for _ in plan_ids)
        conn.execute(
            f"""
            UPDATE account_plans
            SET status = 'account_inactive', is_current = 0, updated_at = ?
            WHERE id IN ({placeholders})
            """,
            [ts, *plan_ids],
        )
        task_cur = conn.execute(
            f"""
            UPDATE scheduled_tasks
            SET status = 'cancelled_account_inactive',
                last_error = ?,
                updated_at = ?
            WHERE plan_id IN ({placeholders})
              AND status IN ('scheduled', 'paused', 'dispatched')
            """,
            [reason, ts, *plan_ids],
        )
        jobs = self._cancel_jobs_for_plan_ids(conn, plan_ids, reason, ts)
        return {"plans": len(plan_ids), "tasks": int(task_cur.rowcount or 0), "jobs": jobs}

    def list_scheduled_tasks(
        self,
        status: Optional[str] = None,
        group_id: Optional[str] = None,
        account_id: Optional[str] = None,
        plan_id: Optional[int] = None,
        limit: int = 100,
    ) -> list[Dict[str, Any]]:
        sql = "SELECT * FROM scheduled_tasks WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        group_id = self.resolve_group_id(group_id)
        if group_id:
            sql += " AND group_id = ?"
            params.append(group_id)
        if account_id:
            sql += " AND account_id = ?"
            params.append(account_id)
        if plan_id is not None:
            sql += " AND plan_id = ?"
            params.append(plan_id)
        sql += " ORDER BY run_at ASC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def dispatch_due_tasks(
        self,
        due_ts: Optional[int] = None,
        limit: int = 100,
        late_grace_seconds: int = 3600,
    ) -> list[Dict[str, Any]]:
        due_ts = due_ts or now_ts()
        dispatched = []
        ts = now_ts()
        cutoff = due_ts - int(late_grace_seconds)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM scheduled_tasks
                WHERE status = 'scheduled' AND run_at <= ?
                ORDER BY run_at ASC
                LIMIT ?
                """,
                (due_ts, limit),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"] or "{}")
                allow_late = bool(payload.get("allow_late"))
                if not allow_late and int(row["run_at"]) < cutoff:
                    conn.execute(
                        """
                        UPDATE scheduled_tasks
                        SET status = 'expired_missed',
                            last_error = 'missed scheduled time; skipped instead of late backfill',
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (ts, row["id"]),
                    )
                    dispatched.append({"scheduled_task_id": row["id"], "status": "expired_missed"})
                    continue
                payload.setdefault("account_id", row["account_id"])
                payload.setdefault("profile_id", row["account_id"])
                payload.setdefault("group_id", row["group_id"])
                payload.setdefault("plan_id", row["plan_id"])
                payload.setdefault("scheduled_task_id", row["id"])
                payload.setdefault("run_at", row["run_at"])
                payload.setdefault("source", "scheduled_task")
                cur = conn.execute(
                    """
                    INSERT INTO jobs (
                        job_type, payload_json, status, target_node_id,
                        created_at, updated_at
                    ) VALUES (?, ?, 'queued', ?, ?, ?)
                    """,
                    (
                        row["job_type"],
                        json.dumps(payload, ensure_ascii=False),
                        row["node_id"],
                        ts,
                        ts,
                    ),
                )
                job_id = int(cur.lastrowid)
                conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET status = 'dispatched', job_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (job_id, ts, row["id"]),
                )
                dispatched.append({"scheduled_task_id": row["id"], "job_id": job_id})
            conn.commit()
        return dispatched

    def _maybe_store_grok_plan(self, job_id: int, result: Dict[str, Any]) -> None:
        job = self.get_job(job_id)
        if not job or job.get("job_type") not in {"generate_grok_plan", "score_grok_plan"}:
            return
        payload = job.get("payload", {})
        is_score_plan = job.get("job_type") == "score_grok_plan"
        ts = now_ts()
        plan_id: Optional[int] = None
        with self.connect() as conn:
            raw = result.get("grok_raw_response") or ""
            parsed = result.get("parsed_plan") or {}
            score = parsed.get("score") if isinstance(parsed, dict) else None
            status = "draft" if raw else (result.get("status") or "completed")
            existing = conn.execute(
                "SELECT id FROM account_plans WHERE source_job_id = ?",
                (job_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE account_plans
                    SET grok_raw_response = ?, parsed_plan_json = ?, status = ?, plan_type = ?, score = ?, updated_at = ?
                    WHERE source_job_id = ?
                    """,
                    (
                        raw,
                        json.dumps(parsed, ensure_ascii=False),
                        status,
                        "score_plan" if is_score_plan else "grok_plan",
                        score,
                        ts,
                        job_id,
                    ),
                )
                plan_id = int(existing["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO account_plans (
                        account_id, group_id, period, start_date, end_date,
                        source_job_id, grok_raw_response, parsed_plan_json,
                        status, plan_type, score, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload.get("account_id") or payload.get("profile_id") or "",
                        payload.get("group_id") or "",
                        payload.get("period") or "weekly",
                        payload.get("start_date"),
                        payload.get("end_date"),
                        job_id,
                        raw,
                        json.dumps(parsed, ensure_ascii=False),
                        status,
                        "score_plan" if is_score_plan else "grok_plan",
                        score,
                        ts,
                        ts,
                    ),
                )
                plan_id = int(cur.lastrowid)
        parsed_plan = result.get("parsed_plan") if isinstance(result.get("parsed_plan"), dict) else {}
        has_parsed_days = bool(parsed_plan.get("days"))
        if plan_id and (result.get("grok_raw_response") or has_parsed_days):
            self.auto_schedule_plan(plan_id, max_days=31)

    def auto_schedule_plan(self, plan_id: int, max_days: int = 31) -> int:
        plan = self.get_plan(plan_id)
        if plan is None:
            return 0
        if self.list_scheduled_tasks(plan_id=plan_id, limit=1):
            return 0
        if plan.get("plan_type") == "score_plan" or (plan.get("parsed_plan") or {}).get("type") == "score_plan":
            tasks = build_tasks_from_score_plan(plan, max_days=max_days)
        else:
            tasks = build_tasks_from_plan(plan, max_days=max_days)
        return self.approve_plan(plan_id, tasks, status="auto_scheduled")

    def update_scheduled_task(self, task_id: int, updates: Dict[str, Any]) -> None:
        allowed_status = {
            "scheduled",
            "paused",
            "dispatched",
            "completed",
            "failed",
            "expired_missed",
            "cancelled_by_user",
            "cancelled_by_new_plan",
        }
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise ValueError(f"scheduled task not found: {task_id}")
            payload = json.loads(row["payload_json"] or "{}")
            if "payload" in updates and isinstance(updates["payload"], dict):
                payload.update(updates["payload"])
            if "metrics" in updates and isinstance(updates["metrics"], dict):
                payload["metrics"] = updates["metrics"]
            run_at = int(updates.get("run_at") or row["run_at"])
            status = updates.get("status") or row["status"]
            if status not in allowed_status:
                raise ValueError(f"invalid task status: {status}")
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET run_at = ?, status = ?, payload_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (run_at, status, json.dumps(payload, ensure_ascii=False), now_ts(), task_id),
            )

    def set_scheduled_task_status(self, task_id: int, status: str) -> None:
        if status not in {"scheduled", "paused", "cancelled_by_user"}:
            raise ValueError(f"unsupported status: {status}")
        ts = now_ts()
        with self.connect() as conn:
            row = conn.execute("SELECT job_id FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise ValueError(f"scheduled task not found: {task_id}")
            conn.execute(
                "UPDATE scheduled_tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, ts, task_id),
            )
            job_id = row["job_id"]
            if status == "cancelled_by_user" and job_id:
                job_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if job_row and job_row["status"] == "queued":
                    conn.execute(
                        "UPDATE jobs SET status = 'cancelled', error = 'cancelled_by_user', updated_at = ? WHERE id = ?",
                        (ts, job_id),
                    )
                elif job_row and job_row["status"] == "leased":
                    conn.execute(
                        "UPDATE jobs SET status = 'cancel_requested', error = 'cancelled_by_user', updated_at = ? WHERE id = ?",
                        (ts, job_id),
                    )

    def auto_schedule_plans(
        self,
        group_id: Optional[str] = None,
        status: Optional[str] = None,
        max_days: int = 31,
        limit: int = 100,
    ) -> Dict[str, Any]:
        plans = self.list_plans(group_id=group_id, status=status, limit=limit)
        scheduled = 0
        skipped = 0
        plan_ids = []
        for plan in plans:
            raw = plan.get("grok_raw_response") or ""
            if not raw:
                skipped += 1
                continue
            count = self.auto_schedule_plan(int(plan["id"]), max_days=max_days)
            if count:
                scheduled += count
                plan_ids.append(int(plan["id"]))
            else:
                skipped += 1
        return {"scheduled_count": scheduled, "plan_ids": plan_ids, "skipped": skipped}
