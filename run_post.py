"""CLI & Heroku Scheduler entrypoint for the Barca X Auto-Poster (Workflow v2).

Recommended Heroku Scheduler job (Every 10 minutes):
    python run_post.py

Useful local commands:
    python run_post.py --check                      # Preflight check (keys, DB, GitHub, ESPN)
    python run_post.py --test-workflow              # Inspect exact matchday/Thursday/weekly decision
    python run_post.py --test-context               # Show live Barca/Madrid/UCL scores & lineups
    python run_post.py --dry-run --force-now        # Generate a sample post without posting to X
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
from app.storage import HistoryStore
from app.workflow import evaluate_workflow
from app.x_client import XClient


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Barca Football Twitter LLM Auto-Poster")
    parser.add_argument("--dry-run", action="store_true", help="Generate post(s) without posting to X.")
    parser.add_argument("--force-now", action="store_true", help="Bypass schedule checks for manual testing.")
    parser.add_argument("--archetype", choices=sorted(ARCHETYPES.keys()), default=None)
    parser.add_argument("--target-club", default=None, help="Override target_club when testing with --force-now.")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--test-context", action="store_true", help="Print live ESPN/RSS context and exit.")
    parser.add_argument("--test-workflow", action="store_true", help="Print exact workflow decision and exit.")
    parser.add_argument("--sync-catalog", action="store_true", help="Sync images -> assets/catalog.json and exit.")
    parser.add_argument("--sync-metrics", action="store_true", help="Fetch latest Views/Likes/Replies from X API for live posts and save to DB.")
    parser.add_argument("--check", action="store_true", help="Run preflight health check and exit.")
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
        return 0

    if args.sync_metrics:
        from app.x_client import X_API_BASE

        store = HistoryStore(settings)
        live = store.get_recent_posts(limit=50, include_dry_runs=False)
        ids = list(dict.fromkeys(r.tweet_id for r in live if r.tweet_id and r.tweet_id.isdigit()))
        if not ids:
            print("No live tweets found to sync.")
            return 0
        xc = XClient(settings)
        resp = xc.session.get(
            f"{X_API_BASE}/tweets",
            params={"ids": ",".join(ids[:50]), "tweet.fields": "public_metrics"},
            auth=xc.auth,
            timeout=20,
        )
        resp.raise_for_status()
        items = resp.json().get("data") or []
        for item in items:
            m = item.get("public_metrics") or {}
            store.update_metrics_by_tweet_id(
                tweet_id=item["id"],
                impressions=int(m.get("impression_count") or 0),
                likes=int(m.get("like_count") or 0),
                replies=int(m.get("reply_count") or 0),
                retweets=int(m.get("retweet_count") or 0),
                quotes=int(m.get("quote_count") or 0),
            )
            print(
                f"  Synced tweet {item['id']}: views={m.get('impression_count', 0)}, "
                f"likes={m.get('like_count', 0)}, replies={m.get('reply_count', 0)}"
            )
        print(f"Updated reach metrics for {len(items)} live post(s).")
        return 0

    if args.check:
        store = HistoryStore(settings)
        behavior = store.get_behavior_config(settings.default_behavior())
        ctx = fetch_live_context()
        decision = evaluate_workflow(behavior, store, ctx)
        posts_7d = store.count_posts_rolling_days(7)
        print("=== Barca X Bot Preflight Check (Workflow v2) ===")
        print(f"Gemini Key #1:    {'SET' if settings.gemini_api_key else 'MISSING'}")
        print(f"Gemini Key #2:    {'SET (fallback ready)' if settings.gemini_api_key_fallback else 'NOT SET'}")
        print(f"Gemini Model:     {behavior.gemini_model} (Google Search={behavior.enable_google_search})")
        print(f"X API Keys:       {'SET (all 4)' if settings.has_x_credentials else 'MISSING (dry-run only)'}")
        print(f"GitHub Images:    {settings.github_repo + '@' + settings.github_branch if settings.has_github_images else 'Local only'}")
        print(f"Database Mode:    {'PostgreSQL (DATABASE_URL)' if settings.database_url else f'Local SQLite ({settings.sqlite_path})'}")
        print(f"Bot Status:       {'ENABLED' if behavior.bot_enabled else 'PAUSED'} | dry_run={behavior.dry_run} | chaos={behavior.chaos_level}")
        print(f"Weekly Budget:    {posts_7d}/{behavior.weekly_post_target} posts in last 7d (avg {posts_7d / 7.0:.2f}/day; target <= 3.71/day)")
        print(f"Off-day Bounds:   min={behavior.non_matchday_min_posts}, max={behavior.non_matchday_max_posts} | slots UTC={behavior.post_hours_utc}")
        by_cat: dict[str, int] = {}
        for a in catalog.assets.values():
            by_cat[a.category] = by_cat.get(a.category, 0) + 1
        print(f"Image Catalog:    {len(catalog.assets)} total {by_cat if by_cat else '(empty)'}")
        print(f"Live Football:    barca={len(ctx.barca_matches)}, madrid={len(ctx.madrid_matches)}, ucl_epl_losses={len(ctx.ucl_recent_epl_losses)}")
        print(f"Workflow Now:     mode={decision.day_mode} | should_post={decision.should_post} | slot={decision.workflow_slot} | target={decision.target_club}")
        print(f"Reason:           {decision.reason}")
        return 0

    context = fetch_live_context()
    if args.test_context:
        print(context.to_prompt_block())
        return 0

    store = HistoryStore(settings)
    behavior = store.get_behavior_config(settings.default_behavior())
    decision = evaluate_workflow(behavior, store, context)

    if args.test_workflow:
        posts_7d = store.count_posts_rolling_days(7)
        print("=== Barca X Bot Workflow Decision ===")
        print(f"Day Mode:         {decision.day_mode}")
        print(f"Should Post Now:  {decision.should_post}")
        print(f"Workflow Slot:    {decision.workflow_slot} (trigger={decision.trigger_type})")
        print(f"Target Club:      {decision.target_club}")
        print(f"Archetype:        {decision.archetype_name}")
        print(f"7-Day Rolling:    {posts_7d}/{behavior.weekly_post_target} ({posts_7d / 7.0:.2f}/day)")
        if decision.today_rotation_clubs:
            print(f"Today's Big-8:    {', '.join(decision.today_rotation_clubs)}")
        print(f"Reason:           {decision.reason}")
        if decision.slot_instructions:
            print(f"Instructions:     {decision.slot_instructions}")
        return 0

    dry_run = args.dry_run or behavior.dry_run or settings.dry_run

    if not args.force_now and not decision.should_post:
        logger.info("Skipping this run [%s]: %s", decision.day_mode, decision.reason)
        return 0

    if decision.focus_match is not None:
        context.focus_match = decision.focus_match

    workflow_slot = decision.workflow_slot if decision.should_post else "manual"
    trigger_type = decision.trigger_type if decision.should_post else "manual"
    target_club = args.target_club or (decision.target_club if decision.should_post else "FC Barcelona")
    chosen_archetype = args.archetype or (decision.archetype_name if decision.should_post else None)
    slot_instructions = decision.slot_instructions if decision.should_post else ""

    logger.info(
        "Proceeding with post [slot=%s | target=%s | trigger=%s]: %s",
        workflow_slot,
        target_club,
        trigger_type,
        decision.reason,
    )

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
            forced_archetype=chosen_archetype,
            target_club=target_club,
            slot_instructions=slot_instructions,
        )

        image_paths: List[Path] = []
        valid_image_ids: List[str] = []
        for asset in gen_result.resolved_images:
            local_file = catalog.ensure_local_file(asset)
            if local_file and local_file.exists():
                image_paths.append(local_file)
                valid_image_ids.append(asset.id)

        print("=" * 72)
        print(
            f"Post {idx + 1}/{count} | Slot: {workflow_slot} | Target: {target_club} "
            f"| Archetype: {gen_result.archetype.name}"
        )
        print(
            f"Gemini: key={gen_result.gemini_key_used.upper()} | model={gen_result.gemini_model_used} "
            + (f"| fallback_reason={gen_result.gemini_fallback_reason}" if gen_result.gemini_fallback_reason else "")
        )
        print(f"Angle:  {gen_result.post.angle_summary}")
        print(f"Chars:  {len(gen_result.post.text)}/280")
        print(f"Images: {[p.name for p in image_paths] or 'None (text-only)'}")
        print("-" * 72)
        print(gen_result.post.text)
        print("=" * 72)

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
                target_club=target_club,
                workflow_slot=workflow_slot,
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
                target_club=target_club,
                workflow_slot=workflow_slot,
            )
            if decision.event_lock and not args.force_now:
                store.mark_match_event_sent(decision.event_lock[0], decision.event_lock[1])
            logger.info("Published to X! %s (slot=%s, target=%s)", result.url, workflow_slot, target_club)

    return 0


if __name__ == "__main__":
    sys.exit(main())
