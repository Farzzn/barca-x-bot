"""Persistence layer shared between the Heroku Python bot & Vercel Next.js Admin UI.

Tables managed automatically in PostgreSQL (Neon/Supabase/Heroku) or local SQLite:
  1. `bot_settings`      — Live behavior & workflow settings controlled from Admin UI
  2. `x_post_history`    — Full post log, target_club, workflow_slot, Gemini key telemetry, & reach metrics
  3. `match_events_sent` — Exact-once locks for 8h Pre-Match, Lineup, HT, FT bursts, & Thursday UCL
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import DEFAULT_ARCHETYPES, RuntimeBehaviorConfig, Settings
from app.context_fetcher import LiveFootballContext
from app.workflow import IST, evaluate_workflow

logger = logging.getLogger(__name__)


@dataclass
class PostRecord:
    tweet_id: str
    archetype: str
    match_state: str
    angle_summary: str
    text: str
    image_ids: List[str]
    dry_run: bool
    created_at_utc: datetime
    trigger_type: str = "scheduled"
    gemini_key_used: str = "primary"
    gemini_fallback_reason: str = ""
    gemini_model: str = ""
    search_grounded: bool = False
    match_label: str = ""
    target_club: str = "FC Barcelona"
    workflow_slot: str = "scheduled"
    impressions: int = 0
    likes: int = 0
    replies: int = 0
    retweets: int = 0
    quotes: int = 0


def _normalize_pg_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _parse_ts(raw_ts) -> datetime:
    if isinstance(raw_ts, str):
        dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    else:
        dt = raw_ts
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class HistoryStore:
    """Shared DB layer for Admin settings, exact-once match locks, club tracking & telemetry."""

    def __init__(self, settings: Settings) -> None:
        self.database_url = _normalize_pg_url(settings.database_url) if settings.database_url else ""
        self.sqlite_path: Path = settings.sqlite_path
        self.use_postgres = bool(self.database_url)

        if not self.use_postgres and settings.is_heroku:
            logger.warning(
                "Running on Heroku without DATABASE_URL: local SQLite resets between one-off "
                "Heroku Scheduler runs. Add a free Neon DATABASE_URL."
            )

        self._init_schema()

    def _connect(self):
        if self.use_postgres:
            import psycopg2

            if "sslmode=" in self.database_url:
                return psycopg2.connect(self.database_url)
            return psycopg2.connect(self.database_url, sslmode="require")

        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.sqlite_path))

    def _placeholder(self) -> str:
        return "%s" if self.use_postgres else "?"

    def _ensure_column(self, cur, table: str, column: str, pg_type: str, sqlite_type: str) -> None:
        if self.use_postgres:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {pg_type}")
        else:
            cur.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cur.fetchall()}
            if column not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sqlite_type}")

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS x_post_history (
                        id SERIAL PRIMARY KEY,
                        tweet_id TEXT,
                        archetype TEXT NOT NULL,
                        match_state TEXT NOT NULL,
                        angle_summary TEXT,
                        text TEXT NOT NULL,
                        image_ids TEXT NOT NULL,
                        dry_run BOOLEAN NOT NULL DEFAULT FALSE,
                        trigger_type TEXT NOT NULL DEFAULT 'scheduled',
                        gemini_key_used TEXT NOT NULL DEFAULT 'primary',
                        gemini_fallback_reason TEXT NOT NULL DEFAULT '',
                        gemini_model TEXT NOT NULL DEFAULT '',
                        search_grounded BOOLEAN NOT NULL DEFAULT FALSE,
                        match_label TEXT NOT NULL DEFAULT '',
                        target_club TEXT NOT NULL DEFAULT 'FC Barcelona',
                        workflow_slot TEXT NOT NULL DEFAULT 'scheduled',
                        impressions INTEGER NOT NULL DEFAULT 0,
                        likes INTEGER NOT NULL DEFAULT 0,
                        replies INTEGER NOT NULL DEFAULT 0,
                        retweets INTEGER NOT NULL DEFAULT 0,
                        quotes INTEGER NOT NULL DEFAULT 0,
                        metrics_updated_at TIMESTAMPTZ,
                        created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_settings (
                        id INTEGER PRIMARY KEY,
                        config_json TEXT NOT NULL,
                        updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS match_events_sent (
                        match_key TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (match_key, event_type)
                    )
                    """
                )
            else:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS x_post_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tweet_id TEXT,
                        archetype TEXT NOT NULL,
                        match_state TEXT NOT NULL,
                        angle_summary TEXT,
                        text TEXT NOT NULL,
                        image_ids TEXT NOT NULL,
                        dry_run INTEGER NOT NULL DEFAULT 0,
                        trigger_type TEXT NOT NULL DEFAULT 'scheduled',
                        gemini_key_used TEXT NOT NULL DEFAULT 'primary',
                        gemini_fallback_reason TEXT NOT NULL DEFAULT '',
                        gemini_model TEXT NOT NULL DEFAULT '',
                        search_grounded INTEGER NOT NULL DEFAULT 0,
                        match_label TEXT NOT NULL DEFAULT '',
                        target_club TEXT NOT NULL DEFAULT 'FC Barcelona',
                        workflow_slot TEXT NOT NULL DEFAULT 'scheduled',
                        impressions INTEGER NOT NULL DEFAULT 0,
                        likes INTEGER NOT NULL DEFAULT 0,
                        replies INTEGER NOT NULL DEFAULT 0,
                        retweets INTEGER NOT NULL DEFAULT 0,
                        quotes INTEGER NOT NULL DEFAULT 0,
                        metrics_updated_at TEXT,
                        created_at_utc TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_settings (
                        id INTEGER PRIMARY KEY,
                        config_json TEXT NOT NULL,
                        updated_at_utc TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS match_events_sent (
                        match_key TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        PRIMARY KEY (match_key, event_type)
                    )
                    """
                )

            # Safe idempotent migrations for existing tables
            self._ensure_column(cur, "x_post_history", "trigger_type", "TEXT NOT NULL DEFAULT 'scheduled'", "TEXT NOT NULL DEFAULT 'scheduled'")
            self._ensure_column(cur, "x_post_history", "gemini_key_used", "TEXT NOT NULL DEFAULT 'primary'", "TEXT NOT NULL DEFAULT 'primary'")
            self._ensure_column(cur, "x_post_history", "gemini_fallback_reason", "TEXT NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(cur, "x_post_history", "gemini_model", "TEXT NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(cur, "x_post_history", "search_grounded", "BOOLEAN NOT NULL DEFAULT FALSE", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(cur, "x_post_history", "match_label", "TEXT NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(cur, "x_post_history", "target_club", "TEXT NOT NULL DEFAULT 'FC Barcelona'", "TEXT NOT NULL DEFAULT 'FC Barcelona'")
            self._ensure_column(cur, "x_post_history", "workflow_slot", "TEXT NOT NULL DEFAULT 'scheduled'", "TEXT NOT NULL DEFAULT 'scheduled'")
            self._ensure_column(cur, "x_post_history", "impressions", "INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(cur, "x_post_history", "likes", "INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(cur, "x_post_history", "replies", "INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(cur, "x_post_history", "retweets", "INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(cur, "x_post_history", "quotes", "INTEGER NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(cur, "x_post_history", "metrics_updated_at", "TIMESTAMPTZ", "TEXT")

            conn.commit()
            cur.close()
        finally:
            conn.close()

    def get_behavior_config(self, defaults: RuntimeBehaviorConfig) -> RuntimeBehaviorConfig:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT config_json FROM bot_settings WHERE id = 1")
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()

        if not row or not row[0]:
            self.save_behavior_config(defaults)
            return defaults

        try:
            data = json.loads(row[0])
        except json.JSONDecodeError:
            return defaults

        def _hours(val) -> List[int]:
            if isinstance(val, list):
                cleaned = sorted({int(h) for h in val if 0 <= int(h) <= 23})
                return cleaned or list(defaults.post_hours_utc)
            return list(defaults.post_hours_utc)

        chaos = str(data.get("chaos_level", defaults.chaos_level)).lower()
        if chaos not in {"balanced", "spicy", "unhinged"}:
            chaos = defaults.chaos_level

        enabled_arch = data.get("enabled_archetypes", defaults.enabled_archetypes)
        if not isinstance(enabled_arch, list) or not enabled_arch:
            enabled_arch = list(DEFAULT_ARCHETYPES)

        return RuntimeBehaviorConfig(
            bot_enabled=bool(data.get("bot_enabled", defaults.bot_enabled)),
            dry_run=bool(data.get("dry_run", defaults.dry_run)),
            max_posts_per_day=max(1, min(15, int(data.get("max_posts_per_day", defaults.max_posts_per_day)))),
            weekly_post_target=max(7, min(70, int(data.get("weekly_post_target", defaults.weekly_post_target)))),
            non_matchday_min_posts=max(0, min(5, int(data.get("non_matchday_min_posts", defaults.non_matchday_min_posts)))),
            non_matchday_max_posts=max(1, min(6, int(data.get("non_matchday_max_posts", defaults.non_matchday_max_posts)))),
            min_minutes_between_posts=max(15, int(data.get("min_minutes_between_posts", defaults.min_minutes_between_posts))),
            matchday_cooldown_minutes=max(10, int(data.get("matchday_cooldown_minutes", defaults.matchday_cooldown_minutes))),
            post_hours_utc=_hours(data.get("post_hours_utc")),
            image_post_ratio=max(0.0, min(1.0, float(data.get("image_post_ratio", defaults.image_post_ratio)))),
            gemini_model=str(data.get("gemini_model", defaults.gemini_model) or defaults.gemini_model),
            enable_google_search=bool(data.get("enable_google_search", defaults.enable_google_search)),
            auto_post_halftime=bool(data.get("auto_post_halftime", defaults.auto_post_halftime)),
            auto_post_fulltime=bool(data.get("auto_post_fulltime", defaults.auto_post_fulltime)),
            auto_post_prematch=bool(data.get("auto_post_prematch", defaults.auto_post_prematch)),
            auto_post_rival_drama=bool(data.get("auto_post_rival_drama", defaults.auto_post_rival_drama)),
            chaos_level=chaos,
            enabled_archetypes=[str(a) for a in enabled_arch],
            custom_directive=str(data.get("custom_directive", defaults.custom_directive) or ""),
        )

    def save_behavior_config(self, cfg: RuntimeBehaviorConfig) -> None:
        now = datetime.now(timezone.utc)
        payload = json.dumps(asdict(cfg))
        p = self._placeholder()
        conn = self._connect()
        try:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    f"""
                    INSERT INTO bot_settings (id, config_json, updated_at_utc)
                    VALUES (1, {p}, {p})
                    ON CONFLICT (id) DO UPDATE
                    SET config_json = EXCLUDED.config_json, updated_at_utc = EXCLUDED.updated_at_utc
                    """,
                    (payload, now),
                )
            else:
                cur.execute(
                    f"""
                    INSERT INTO bot_settings (id, config_json, updated_at_utc)
                    VALUES (1, {p}, {p})
                    ON CONFLICT (id) DO UPDATE
                    SET config_json = excluded.config_json, updated_at_utc = excluded.updated_at_utc
                    """,
                    (payload, now.isoformat()),
                )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def has_sent_match_event(self, match_key: str, event_type: str) -> bool:
        p = self._placeholder()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT 1 FROM match_events_sent WHERE match_key = {p} AND event_type = {p} LIMIT 1",
                (match_key, event_type),
            )
            exists = cur.fetchone() is not None
            cur.close()
            return exists
        finally:
            conn.close()

    def last_match_event_time(self, match_key: str, event_types: List[str]) -> Optional[datetime]:
        if not event_types:
            return None
        p = self._placeholder()
        placeholders = ", ".join(p for _ in event_types)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT created_at_utc FROM match_events_sent
                WHERE match_key = {p} AND event_type IN ({placeholders})
                ORDER BY created_at_utc DESC
                LIMIT 1
                """,
                tuple([match_key, *event_types]),
            )
            row = cur.fetchone()
            cur.close()
            return _parse_ts(row[0]) if row and row[0] else None
        finally:
            conn.close()

    def mark_match_event_sent(
        self, match_key: str, event_type: str, created_at_utc: Optional[datetime] = None
    ) -> None:
        now = (created_at_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        p = self._placeholder()
        conn = self._connect()
        try:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    f"""
                    INSERT INTO match_events_sent (match_key, event_type, created_at_utc)
                    VALUES ({p}, {p}, {p})
                    ON CONFLICT (match_key, event_type) DO NOTHING
                    """,
                    (match_key, event_type, now),
                )
            else:
                cur.execute(
                    f"""
                    INSERT OR IGNORE INTO match_events_sent (match_key, event_type, created_at_utc)
                    VALUES ({p}, {p}, {p})
                    """,
                    (match_key, event_type, now.isoformat()),
                )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def record_post(
        self,
        tweet_id: str,
        archetype: str,
        match_state: str,
        angle_summary: str,
        text: str,
        image_ids: List[str],
        dry_run: bool = False,
        created_at_utc: Optional[datetime] = None,
        trigger_type: str = "scheduled",
        gemini_key_used: str = "primary",
        gemini_fallback_reason: str = "",
        gemini_model: str = "",
        search_grounded: bool = False,
        match_label: str = "",
        target_club: str = "FC Barcelona",
        workflow_slot: str = "scheduled",
    ) -> None:
        ts = (created_at_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        p = self._placeholder()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO x_post_history (
                    tweet_id, archetype, match_state, angle_summary, text, image_ids,
                    dry_run, trigger_type, gemini_key_used, gemini_fallback_reason,
                    gemini_model, search_grounded, match_label, target_club, workflow_slot, created_at_utc
                )
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
                """,
                (
                    tweet_id,
                    archetype,
                    match_state,
                    angle_summary,
                    text,
                    json.dumps(image_ids),
                    bool(dry_run) if self.use_postgres else (1 if dry_run else 0),
                    trigger_type,
                    gemini_key_used,
                    gemini_fallback_reason,
                    gemini_model,
                    bool(search_grounded) if self.use_postgres else (1 if search_grounded else 0),
                    match_label,
                    target_club,
                    workflow_slot,
                    ts if self.use_postgres else ts.isoformat(),
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def get_recent_posts(self, limit: int = 15, include_dry_runs: bool = False) -> List[PostRecord]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            where = "" if include_dry_runs else ("WHERE dry_run = FALSE" if self.use_postgres else "WHERE dry_run = 0")
            p = self._placeholder()
            cur.execute(
                f"""
                SELECT tweet_id, archetype, match_state, angle_summary, text, image_ids,
                       dry_run, created_at_utc, trigger_type, gemini_key_used,
                       gemini_fallback_reason, gemini_model, search_grounded, match_label,
                       target_club, workflow_slot, impressions, likes, replies, retweets, quotes
                FROM x_post_history
                {where}
                ORDER BY id DESC
                LIMIT {p}
                """,
                (limit,),
            )
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()

        records: List[PostRecord] = []
        for row in rows:
            records.append(
                PostRecord(
                    tweet_id=row[0] or "",
                    archetype=row[1],
                    match_state=row[2],
                    angle_summary=row[3] or "",
                    text=row[4],
                    image_ids=json.loads(row[5] or "[]"),
                    dry_run=bool(row[6]),
                    created_at_utc=_parse_ts(row[7]),
                    trigger_type=row[8] or "scheduled",
                    gemini_key_used=row[9] or "primary",
                    gemini_fallback_reason=row[10] or "",
                    gemini_model=row[11] or "",
                    search_grounded=bool(row[12]),
                    match_label=row[13] or "",
                    target_club=row[14] or "FC Barcelona",
                    workflow_slot=row[15] or "scheduled",
                    impressions=int(row[16] or 0),
                    likes=int(row[17] or 0),
                    replies=int(row[18] or 0),
                    retweets=int(row[19] or 0),
                    quotes=int(row[20] or 0),
                )
            )
        return records

    def get_recent_target_clubs(self, limit: int = 12) -> List[str]:
        return [r.target_club for r in self.get_recent_posts(limit=limit, include_dry_runs=False) if r.target_club]

    def count_posts_since(self, since_utc: datetime) -> int:
        p = self._placeholder()
        conn = self._connect()
        try:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    f"SELECT COUNT(*) FROM x_post_history WHERE dry_run = FALSE AND created_at_utc >= {p}",
                    (since_utc,),
                )
            else:
                cur.execute(
                    f"SELECT COUNT(*) FROM x_post_history WHERE dry_run = 0 AND created_at_utc >= {p}",
                    (since_utc.isoformat(),),
                )
            count = int(cur.fetchone()[0])
            cur.close()
            return count
        finally:
            conn.close()

    def count_posts_today_utc(self, now: Optional[datetime] = None) -> int:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return self.count_posts_since(current.replace(hour=0, minute=0, second=0, microsecond=0))

    def count_posts_today_ist(self, now: Optional[datetime] = None) -> int:
        current_ist = (now or datetime.now(timezone.utc)).astimezone(IST)
        start_ist = current_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.count_posts_since(start_ist.astimezone(timezone.utc))

    def count_posts_rolling_days(self, days: int = 7, now: Optional[datetime] = None) -> int:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return self.count_posts_since(current - timedelta(days=days))

    def last_live_post_time(self) -> Optional[datetime]:
        recent = self.get_recent_posts(limit=1, include_dry_runs=False)
        return recent[0].created_at_utc if recent else None


def decide_run_action(
    behavior: RuntimeBehaviorConfig,
    store: HistoryStore,
    context: LiveFootballContext,
    now: Optional[datetime] = None,
) -> Tuple[bool, str, str]:
    """Backwards-compatible wrapper around `evaluate_workflow`."""
    d = evaluate_workflow(behavior, store, context, now=now)
    return d.should_post, d.trigger_type, d.reason


def should_post_now(
    settings: Settings,
    store: HistoryStore,
    context: LiveFootballContext,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    d = evaluate_workflow(settings.default_behavior(), store, context, now=now)
    return d.should_post, d.reason
