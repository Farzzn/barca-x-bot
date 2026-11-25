"""Persistence layer shared between the Heroku Python bot & Vercel Next.js Admin UI.

Tables managed automatically in PostgreSQL (Neon/Supabase/Heroku) or local SQLite:
  1. `bot_settings`      — Live behavior settings controlled from the Admin Dashboard
  2. `x_post_history`    — Full post log + Gemini API key telemetry (primary vs fallback)
  3. `match_events_sent` — Exact-once tracking for Half-Time (HT) & Full-Time (FT) triggers
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import DEFAULT_ARCHETYPES, RuntimeBehaviorConfig, Settings
from app.context_fetcher import LiveFootballContext, MatchState

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


def _normalize_pg_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


class HistoryStore:
    """Shared DB layer for live Admin settings, HT/FT event locks, and post/API-key telemetry."""

    def __init__(self, settings: Settings) -> None:
        self.database_url = _normalize_pg_url(settings.database_url) if settings.database_url else ""
        self.sqlite_path: Path = settings.sqlite_path
        self.use_postgres = bool(self.database_url)

        if not self.use_postgres and settings.is_heroku:
            logger.warning(
                "Running on Heroku without DATABASE_URL: local SQLite resets between one-off "
                "Heroku Scheduler runs. Add a free Neon or Supabase DATABASE_URL so Heroku and "
                "your Vercel Admin Dashboard share settings, HT/FT locks, and API-key telemetry."
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

            # Safe idempotent migrations for existing tables created in v1
            self._ensure_column(cur, "x_post_history", "trigger_type", "TEXT NOT NULL DEFAULT 'scheduled'", "TEXT NOT NULL DEFAULT 'scheduled'")
            self._ensure_column(cur, "x_post_history", "gemini_key_used", "TEXT NOT NULL DEFAULT 'primary'", "TEXT NOT NULL DEFAULT 'primary'")
            self._ensure_column(cur, "x_post_history", "gemini_fallback_reason", "TEXT NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(cur, "x_post_history", "gemini_model", "TEXT NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(cur, "x_post_history", "search_grounded", "BOOLEAN NOT NULL DEFAULT FALSE", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(cur, "x_post_history", "match_label", "TEXT NOT NULL DEFAULT ''", "TEXT NOT NULL DEFAULT ''")

            conn.commit()
            cur.close()
        finally:
            conn.close()

    def get_behavior_config(self, defaults: RuntimeBehaviorConfig) -> RuntimeBehaviorConfig:
        """Read live settings saved from the Admin UI, seeding defaults if empty."""
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
            min_minutes_between_posts=max(
                15, int(data.get("min_minutes_between_posts", defaults.min_minutes_between_posts))
            ),
            matchday_cooldown_minutes=max(
                10, int(data.get("matchday_cooldown_minutes", defaults.matchday_cooldown_minutes))
            ),
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

    def mark_match_event_sent(self, match_key: str, event_type: str) -> None:
        now = datetime.now(timezone.utc)
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
                    gemini_model, search_grounded, match_label, created_at_utc
                )
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
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
                       gemini_fallback_reason, gemini_model, search_grounded, match_label
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
            raw_ts = row[7]
            if isinstance(raw_ts, str):
                dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            else:
                dt = raw_ts
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            records.append(
                PostRecord(
                    tweet_id=row[0] or "",
                    archetype=row[1],
                    match_state=row[2],
                    angle_summary=row[3] or "",
                    text=row[4],
                    image_ids=json.loads(row[5] or "[]"),
                    dry_run=bool(row[6]),
                    created_at_utc=dt.astimezone(timezone.utc),
                    trigger_type=row[8] or "scheduled",
                    gemini_key_used=row[9] or "primary",
                    gemini_fallback_reason=row[10] or "",
                    gemini_model=row[11] or "",
                    search_grounded=bool(row[12]),
                    match_label=row[13] or "",
                )
            )
        return records

    def count_posts_today_utc(self, now: Optional[datetime] = None) -> int:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start_of_day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        p = self._placeholder()
        conn = self._connect()
        try:
            cur = conn.cursor()
            if self.use_postgres:
                cur.execute(
                    f"SELECT COUNT(*) FROM x_post_history WHERE dry_run = FALSE AND created_at_utc >= {p}",
                    (start_of_day,),
                )
            else:
                cur.execute(
                    f"SELECT COUNT(*) FROM x_post_history WHERE dry_run = 0 AND created_at_utc >= {p}",
                    (start_of_day.isoformat(),),
                )
            count = int(cur.fetchone()[0])
            cur.close()
            return count
        finally:
            conn.close()

    def last_live_post_time(self) -> Optional[datetime]:
        recent = self.get_recent_posts(limit=1, include_dry_runs=False)
        return recent[0].created_at_utc if recent else None


def decide_run_action(
    behavior: RuntimeBehaviorConfig,
    store: HistoryStore,
    context: LiveFootballContext,
    now: Optional[datetime] = None,
) -> Tuple[bool, str, str]:
    """Decide whether this scheduler tick should post, returning `(allowed, trigger_type, reason)`."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if not behavior.bot_enabled:
        return False, "paused", "Bot is currently paused via Admin Dashboard (`bot_enabled=false`)."

    posts_today = store.count_posts_today_utc(current)

    # 1. HIGH-PRIORITY INSTANT TRIGGERS: Half-Time & Full-Time (peak X engagement moments!)
    instant_event = context.instant_event_type
    if instant_event and context.focus_match:
        enabled = (
            behavior.auto_post_halftime
            if instant_event == "halftime"
            else behavior.auto_post_fulltime
        )
        if enabled and not store.has_sent_match_event(context.focus_match.match_key, instant_event):
            # Allow up to +2 bonus posts beyond daily cap so HT/FT reactions never miss!
            if posts_today < behavior.max_posts_per_day + 2:
                return (
                    True,
                    instant_event,
                    f"Instant {instant_event.upper()} trigger fired for {context.focus_match.summary_line()}!",
                )

    # 2. Regular daily cap check
    if posts_today >= behavior.max_posts_per_day:
        return False, "cap", f"Daily cap reached ({posts_today}/{behavior.max_posts_per_day} posts today UTC)."

    # 3. Cooldown check (shorter during live/post match windows)
    last_post = store.last_live_post_time()
    is_hot = context.is_matchday_hot and (
        context.match_state != MatchState.BARCA_PRE_MATCH or behavior.auto_post_prematch
    )
    cooldown = behavior.matchday_cooldown_minutes if is_hot else behavior.min_minutes_between_posts
    if last_post is not None:
        elapsed_minutes = (current - last_post).total_seconds() / 60.0
        if elapsed_minutes < cooldown:
            return (
                False,
                "cooldown",
                f"Cooldown active ({elapsed_minutes:.1f}m elapsed < {cooldown}m required for state={context.match_state.value}).",
            )

    # 4. Matchday hot window posts immediately once cooldown clears
    if is_hot:
        return True, "matchday", f"Barca matchday window active ({context.match_state.value}); posting allowed."

    # 5. Rival drama optional check
    if context.match_state == MatchState.RIVAL_MATCH_DRAMA and not behavior.auto_post_rival_drama:
        # Downgrade to normal UTC slot pacing
        pass

    # 6. Quiet-day UTC slot check (safe even when Heroku Scheduler runs every 10 minutes!)
    if current.hour not in behavior.post_hours_utc:
        return (
            False,
            "off_hour",
            f"Current UTC hour {current.hour:02d} not in POST_HOURS_UTC={behavior.post_hours_utc} "
            f"(no active Barca match).",
        )

    return True, "scheduled", f"Scheduled UTC hour {current.hour:02d} matched ({posts_today}/{behavior.max_posts_per_day} today)."


def should_post_now(
    settings: Settings,
    store: HistoryStore,
    context: LiveFootballContext,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    """Backwards-compatible wrapper around `decide_run_action`."""
    allowed, _, reason = decide_run_action(settings.default_behavior(), store, context, now=now)
    return allowed, reason
