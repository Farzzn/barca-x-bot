"""Environment & database-backed configuration for the Barca X Auto-Poster."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ARCHETYPES = [
    "pre_match_delusion",
    "post_win_hype",
    "post_drop_meltdown",
    "rival_comparison_bait",
    "rival_match_slander",
    "epl_intensity_slander",
    "would_you_rather",
    "unpopular_one_percent",
]


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _parse_float(value: Optional[str], default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        return float(value.strip())
    except ValueError:
        return default


def _parse_hours(value: Optional[str], default: List[int]) -> List[int]:
    if value is None or not value.strip():
        return list(default)
    hours: List[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour = int(part)
            if 0 <= hour <= 23:
                hours.append(hour)
        except ValueError:
            continue
    return sorted(set(hours)) or list(default)


@dataclass
class RuntimeBehaviorConfig:
    """Behavior settings editable live from the Vercel Next.js Admin Interface."""

    bot_enabled: bool = True
    dry_run: bool = False
    max_posts_per_day: int = 6
    weekly_post_target: int = 26  # 26 ± 4 posts/week -> avg <= 3.71/day
    non_matchday_min_posts: int = 1
    non_matchday_max_posts: int = 3
    min_minutes_between_posts: int = 150
    matchday_cooldown_minutes: int = 20
    post_hours_utc: List[int] = field(default_factory=lambda: [6, 11, 16])
    image_post_ratio: float = 0.60
    gemini_model: str = "gemini-3.5-flash-lite"
    enable_google_search: bool = False
    auto_post_halftime: bool = True
    auto_post_fulltime: bool = True
    auto_post_prematch: bool = True
    auto_post_rival_drama: bool = True
    chaos_level: str = "spicy"  # balanced | spicy | unhinged
    enabled_archetypes: List[str] = field(default_factory=lambda: list(DEFAULT_ARCHETYPES))
    custom_directive: str = ""


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from this project's .env / Heroku Config Vars."""

    gemini_api_key: str = ""
    gemini_api_key_fallback: str = ""
    gemini_model: str = "gemini-4-hydrogen"
    enable_google_search: bool = True

    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_token_secret: str = ""

    github_repo: str = ""  # e.g. "username/barca-x-bot"
    github_branch: str = "main"
    github_token: str = ""
    github_images_prefix: str = "assets/images"

    bot_enabled: bool = True
    max_posts_per_day: int = 6
    weekly_post_target: int = 26
    non_matchday_min_posts: int = 1
    non_matchday_max_posts: int = 3
    min_minutes_between_posts: int = 150
    matchday_cooldown_minutes: int = 20
    post_hours_utc: List[int] = field(default_factory=lambda: [6, 11, 16])
    image_post_ratio: float = 0.60
    auto_post_halftime: bool = True
    auto_post_fulltime: bool = True
    auto_post_prematch: bool = True
    auto_post_rival_drama: bool = True
    chaos_level: str = "spicy"
    custom_directive: str = ""

    database_url: str = ""
    sqlite_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "history.db")
    assets_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "assets" / "images")
    catalog_path: Path = field(default_factory=lambda: PROJECT_ROOT / "assets" / "catalog.json")
    github_cache_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "github_images")

    dry_run: bool = False
    log_level: str = "INFO"

    @property
    def has_x_credentials(self) -> bool:
        return all(
            [
                self.x_api_key,
                self.x_api_secret,
                self.x_access_token,
                self.x_access_token_secret,
            ]
        )

    @property
    def has_github_images(self) -> bool:
        return bool(self.github_repo)

    @property
    def is_heroku(self) -> bool:
        return bool(os.getenv("DYNO"))

    def default_behavior(self) -> RuntimeBehaviorConfig:
        return RuntimeBehaviorConfig(
            bot_enabled=self.bot_enabled,
            dry_run=self.dry_run,
            max_posts_per_day=self.max_posts_per_day,
            weekly_post_target=self.weekly_post_target,
            non_matchday_min_posts=self.non_matchday_min_posts,
            non_matchday_max_posts=self.non_matchday_max_posts,
            min_minutes_between_posts=self.min_minutes_between_posts,
            matchday_cooldown_minutes=self.matchday_cooldown_minutes,
            post_hours_utc=list(self.post_hours_utc),
            image_post_ratio=self.image_post_ratio,
            gemini_model=self.gemini_model,
            enable_google_search=self.enable_google_search,
            auto_post_halftime=self.auto_post_halftime,
            auto_post_fulltime=self.auto_post_fulltime,
            auto_post_prematch=self.auto_post_prematch,
            auto_post_rival_drama=self.auto_post_rival_drama,
            chaos_level=self.chaos_level,
            enabled_archetypes=list(DEFAULT_ARCHETYPES),
            custom_directive=self.custom_directive,
        )


def load_settings(env_file: Optional[Path] = None) -> Settings:
    """Load settings strictly from this project's own .env file (never global GEMINI_API_KEY).

    Locally, reads BARCA_GEMINI_API_KEY + BARCA_GEMINI_API_KEY_2 (or GEMINI_API_KEY *only* if
    explicitly defined in barca-x-bot/.env) so we never spend quota on another project's key.
    On Heroku (DYNO set), accepts either BARCA_GEMINI_API_KEY or GEMINI_API_KEY from Config Vars.
    """
    target_env = env_file if env_file is not None else PROJECT_ROOT / ".env"
    local_env_values = dotenv_values(target_env) if target_env.exists() else {}
    if target_env.exists():
        load_dotenv(dotenv_path=target_env, override=True)

    is_heroku = bool(os.getenv("DYNO"))

    primary_key = (local_env_values.get("BARCA_GEMINI_API_KEY") or os.getenv("BARCA_GEMINI_API_KEY") or "").strip()
    if not primary_key and (is_heroku or "GEMINI_API_KEY" in local_env_values):
        primary_key = (local_env_values.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()

    fallback_key = (
        local_env_values.get("BARCA_GEMINI_API_KEY_2")
        or local_env_values.get("BARCA_GEMINI_API_KEY_FALLBACK")
        or os.getenv("BARCA_GEMINI_API_KEY_2")
        or os.getenv("BARCA_GEMINI_API_KEY_FALLBACK")
        or ""
    ).strip()

    chaos = (os.getenv("CHAOS_LEVEL") or "spicy").strip().lower()
    if chaos not in {"balanced", "spicy", "unhinged"}:
        chaos = "spicy"

    return Settings(
        gemini_api_key=primary_key,
        gemini_api_key_fallback=fallback_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-4-hydrogen").strip() or "gemini-4-hydrogen",
        enable_google_search=_parse_bool(os.getenv("ENABLE_GOOGLE_SEARCH"), default=True),
        x_api_key=os.getenv("X_API_KEY", "").strip(),
        x_api_secret=os.getenv("X_API_SECRET", "").strip(),
        x_access_token=os.getenv("X_ACCESS_TOKEN", "").strip(),
        x_access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET", "").strip(),
        github_repo=os.getenv("GITHUB_REPO", "").strip(),
        github_branch=os.getenv("GITHUB_BRANCH", "main").strip() or "main",
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        github_images_prefix=os.getenv("GITHUB_IMAGES_PREFIX", "assets/images").strip().strip("/") or "assets/images",
        bot_enabled=_parse_bool(os.getenv("BOT_ENABLED"), default=True),
        max_posts_per_day=max(1, min(15, _parse_int(os.getenv("MAX_POSTS_PER_DAY"), 6))),
        weekly_post_target=max(7, min(70, _parse_int(os.getenv("WEEKLY_POST_TARGET"), 26))),
        non_matchday_min_posts=max(0, min(5, _parse_int(os.getenv("NON_MATCHDAY_MIN_POSTS"), 1))),
        non_matchday_max_posts=max(1, min(6, _parse_int(os.getenv("NON_MATCHDAY_MAX_POSTS"), 3))),
        min_minutes_between_posts=max(15, _parse_int(os.getenv("MIN_MINUTES_BETWEEN_POSTS"), 150)),
        matchday_cooldown_minutes=max(10, _parse_int(os.getenv("MATCHDAY_COOLDOWN_MINUTES"), 20)),
        post_hours_utc=_parse_hours(os.getenv("POST_HOURS_UTC"), [6, 11, 16]),
        image_post_ratio=max(0.0, min(1.0, _parse_float(os.getenv("IMAGE_POST_RATIO"), 0.60))),
        auto_post_halftime=_parse_bool(os.getenv("AUTO_POST_HALFTIME"), default=True),
        auto_post_fulltime=_parse_bool(os.getenv("AUTO_POST_FULLTIME"), default=True),
        auto_post_prematch=_parse_bool(os.getenv("AUTO_POST_PREMATCH"), default=True),
        auto_post_rival_drama=_parse_bool(os.getenv("AUTO_POST_RIVAL_DRAMA"), default=True),
        chaos_level=chaos,
        custom_directive=os.getenv("CUSTOM_DIRECTIVE", "").strip(),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        dry_run=_parse_bool(os.getenv("DRY_RUN"), default=False),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
