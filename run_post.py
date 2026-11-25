"""CLI & Heroku Scheduler entrypoint for the Barca X Auto-Poster.

Recommended Heroku Scheduler job (Every 10 minutes — catches every Half-Time & Full-Time!):
    python run_post.py

Useful local commands:
    python run_post.py --check                      # Preflight check (keys, DB, GitHub, ESPN)
    python run_post.py --test-context               # Show live Barca/rival scores & mood state
    python run_post.py --sync-catalog               # Sync local/GitHub images -> assets/catalog.json
    python run_post.py --dry-run --force-now        # Generate a sample post without posting to X
    python run_post.py --dry-run --force-now --trigger fulltime
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from app.config import load_settings
from app.context_fetcher import fetch_live_context
from app.generator import PostGenerator
from app.image_catalog import ImageCatalog
from app.persona import ARCHETYPES
from app.storage import HistoryStore, decide_run_action
from app.x_client import XClient


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Barca Football Twitter LLM Auto-Poster")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate post(s) and log them locally without calling X API.",
    )
    parser.add_argument(
        "--force-now",
        action="store_true",
        help="Bypass POST_HOURS_UTC, daily cap, pause switch, and cooldown checks.",
    )
    parser.add_argument(
        "--archetype",
        choices=sorted(ARCHETYPES.keys()),
        default=None,
        help="Force a specific banter archetype instead of auto-selecting by match state.",
    )
    parser.add_argument(
        "--trigger",
        choices=["scheduled", "halftime", "fulltime", "matchday", "manual"],
        default=None,
        help="Override trigger_type label (useful for testing HT/FT prompts in --dry-run).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of posts to generate (only >1 allowed with --dry-run).",
    )
    parser.add_argument(
        "--test-context",
        action="store_true",
        help="Fetch and display live Barca/rival match context and exit.",
    )
    parser.add_argument(
        "--sync-catalog",
        action="store_true",
        help="Scan local/GitHub images and write assets/catalog.json, then exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run a preflight health check on env vars, DB, GitHub image catalog, and live feeds.",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger("run_post")

    catalog = ImageCatalog(
        assets_dir=settings.assets_dir,
        catalog_path=settings.catalog_path,
        github_repo=settings.github_repo,
        github_branch=settings.github_branch,
        github_token=settings.github_token,
        github_images_prefix=settings.github_images_prefix,
        cache_dir=settings.github_cache_dir,
    )

    if args.sync_catalog:
        out_path = catalog.sync_catalog_file()
        print(f"Synced {len(catalog.assets)} image(s) to {out_path}")
        for asset in catalog.assets.values():
            source = "GitHub" if asset.github_repo_path else "Local"
            print(f"  - [{source}] {asset.id}: {asset.description} [{asset.relative_path}]")
        if not catalog.assets:
            print("  (No images yet — upload via Admin UI or drop photos into assets/images/barca/ or rivals/)")
        return 0

    if args.check:
        store = HistoryStore(settings)
        behavior = store.get_behavior_config(settings.default_behavior())
        print("=== Barca X Bot Preflight Check ===")
        print(f"Gemini Key #1:    {'SET' if settings.gemini_api_key else 'MISSING'}")
        print(f"Gemini Key #2:    {'SET (fallback ready)' if settings.gemini_api_key_fallback else 'NOT SET (optional fallback)'}")
        print(f"Gemini Model:     {behavior.gemini_model} (Google Search={behavior.enable_google_search})")
        print(f"X API Keys:       {'SET (all 4)' if settings.has_x_credentials else 'MISSING (dry-run only)'}")
        print(f"GitHub Images:    {settings.github_repo + '@' + settings.github_branch if settings.has_github_images else 'Local folder only'}")
        print(f"Database Mode:    {'PostgreSQL (DATABASE_URL)' if settings.database_url else f'Local SQLite ({settings.sqlite_path})'}")
        print(f"Bot Status:       {'ENABLED' if behavior.bot_enabled else 'PAUSED'} | dry_run={behavior.dry_run} | chaos={behavior.chaos_level}")
        print(f"Match Triggers:   HT={behavior.auto_post_halftime} | FT={behavior.auto_post_fulltime} | Pre={behavior.auto_post_prematch} | Rival={behavior.auto_post_rival_drama}")
        print(f"Daily Schedule:   max {behavior.max_posts_per_day}/day | POST_HOURS_UTC={behavior.post_hours_utc}")
        by_cat: dict[str, int] = {}
        for a in catalog.assets.values():
            by_cat[a.category] = by_cat.get(a.category, 0) + 1
        print(f"Image Catalog:    {len(catalog.assets)} total {by_cat if by_cat else '(empty)'}")
        print(f"DB Connection:    OK ({store.count_posts_today_utc()} live posts today UTC)")
        ctx = fetch_live_context()
        print(f"Live Football:    OK (state={ctx.match_state.value}, barca={len(ctx.barca_matches)}, rivals={len(ctx.rival_matches)}, headlines={len(ctx.headlines)})")
        return 0

    context = fetch_live_context()
    if args.test_context:
        print(context.to_prompt_block())
        return 0

    store = HistoryStore(settings)
    behavior = store.get_behavior_config(settings.default_behavior())
    dry_run = args.dry_run or behavior.dry_run or settings.dry_run

    trigger_type = args.trigger or "manual"
    if not args.force_now:
        allowed, auto_trigger, reason = decide_run_action(behavior, store, context)
        if not allowed:
            logger.info("Skipping this run: %s", reason)
            return 0
        trigger_type = args.trigger or auto_trigger
        logger.info("Proceeding with post [trigger=%s]: %s", trigger_type, reason)

    generator = PostGenerator(settings)
    x_client = None if dry_run else XClient(settings)

    count = max(1, args.count)
    if count > 1 and not dry_run:
        logger.warning("Refusing --count > 1 outside --dry-run to protect against accidental spamming.")
        count = 1

    match_label = context.focus_match.summary_line() if context.focus_match else ""

    for idx in range(count):
        recent_records = store.get_recent_posts(limit=15, include_dry_runs=dry_run)
        recent_texts = [r.text for r in recent_records]
        recent_archetypes = [r.archetype for r in recent_records]

        gen_result = generator.generate(
            context=context,
            catalog=catalog,
            recent_posts=recent_texts,
            recent_archetypes=recent_archetypes,
            behavior=behavior,
            trigger_type=trigger_type,
            forced_archetype=args.archetype,
        )

        # Resolve local files (downloading from GitHub on the fly if uploaded via Admin UI!)
        image_paths: List[Path] = []
        valid_image_ids: List[str] = []
        for asset in gen_result.resolved_images:
            local_file = catalog.ensure_local_file(asset)
            if local_file and local_file.exists():
                image_paths.append(local_file)
                valid_image_ids.append(asset.id)

        print("=" * 68)
        print(
            f"Post {idx + 1}/{count} | Trigger: {trigger_type} | State: {context.match_state.value} "
            f"| Archetype: {gen_result.archetype.name}"
        )
        print(
            f"Gemini: key={gen_result.gemini_key_used.upper()} | model={gen_result.gemini_model_used} "
            f"| search={gen_result.search_grounded}"
            + (f" | fallback_reason={gen_result.gemini_fallback_reason}" if gen_result.gemini_fallback_reason else "")
        )
        print(f"Angle:  {gen_result.post.angle_summary}")
        print(f"Chars:  {len(gen_result.post.text)}/280")
        print(f"Images: {[p.name for p in image_paths] or 'None (text-only)'}")
        print("-" * 68)
        print(gen_result.post.text)
        print("=" * 68)

        if dry_run:
            store.record_post(
                tweet_id="dry-run",
                archetype=gen_result.archetype.name,
                match_state=context.match_state.value,
                angle_summary=gen_result.post.angle_summary,
                text=gen_result.post.text,
                image_ids=valid_image_ids,
                dry_run=True,
                trigger_type=trigger_type,
                gemini_key_used=gen_result.gemini_key_used,
                gemini_fallback_reason=gen_result.gemini_fallback_reason,
                gemini_model=gen_result.gemini_model_used,
                search_grounded=gen_result.search_grounded,
                match_label=match_label,
            )
            logger.info("Dry run complete — nothing posted to X.")
        else:
            assert x_client is not None
            result = x_client.create_post(gen_result.post.text, image_paths=image_paths)
            store.record_post(
                tweet_id=result.tweet_id,
                archetype=gen_result.archetype.name,
                match_state=context.match_state.value,
                angle_summary=gen_result.post.angle_summary,
                text=gen_result.post.text,
                image_ids=valid_image_ids,
                dry_run=False,
                trigger_type=trigger_type,
                gemini_key_used=gen_result.gemini_key_used,
                gemini_fallback_reason=gen_result.gemini_fallback_reason,
                gemini_model=gen_result.gemini_model_used,
                search_grounded=gen_result.search_grounded,
                match_label=match_label,
            )
            if trigger_type in {"halftime", "fulltime"} and context.focus_match:
                store.mark_match_event_sent(context.focus_match.match_key, trigger_type)
            logger.info("Published to X! %s (media_ids=%s)", result.url, result.media_ids)

    return 0


if __name__ == "__main__":
    sys.exit(main())
