"""Automated tests for barca-x-bot Workflow v2 (Barça/Madrid matchdays, Thursday UCL, Big-8 weekly pacing, dual keys, X v2)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from app.config import RuntimeBehaviorConfig, Settings, load_settings
from app.context_fetcher import (
    BIG_EIGHT_RIVALS,
    LineupInfo,
    LiveFootballContext,
    MatchInfo,
    MatchState,
    parse_espn_events,
)
from app.generator import GeneratedPost, PostGenerator, is_too_similar, sanitize_and_validate_text
from app.image_catalog import ImageCatalog, infer_metadata_from_path, prepare_image_for_x
from app.storage import HistoryStore
from app.workflow import evaluate_workflow, pick_daily_big_eight_clubs
from app.x_client import XClient


def _xi(team: str) -> LineupInfo:
    return LineupInfo(
        team_name=team,
        formation="4-3-3",
        starters=[f"{team} Player {i}" for i in range(1, 12)],
    )


class TestWorkflowV2(unittest.TestCase):
    def test_barca_matchday_win_is_4_posts_and_draw_is_6_posts(self):
        with TemporaryDirectory() as tmp:
            settings = Settings(sqlite_path=Path(tmp) / "history.db")
            store = HistoryStore(settings)
            behavior = settings.default_behavior()
            ko = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)

            # 1) 8h before kickoff -> barca_prematch_8h
            m_pre = MatchInfo(
                competition="La Liga", league_slug="esp.1", home_team="Barcelona", away_team="Valencia",
                home_score=None, away_score=None, status_state="pre", status_detail="20:00 UTC",
                kickoff_utc=ko, event_id="1001",
            )
            ctx = LiveFootballContext(fetched_at_utc=ko - timedelta(hours=7), match_state=MatchState.BARCA_PRE_MATCH, barca_matches=[m_pre])
            d1 = evaluate_workflow(behavior, store, ctx, now=ko - timedelta(hours=7))
            self.assertTrue(d1.should_post)
            self.assertEqual(d1.workflow_slot, "barca_prematch_8h")
            self.assertEqual(d1.target_club, "FC Barcelona")
            store.mark_match_event_sent(*d1.event_lock, created_at_utc=ko - timedelta(hours=7))

            # Next tick 3h before KO -> skip (no off-day spam on matchday!)
            d_skip = evaluate_workflow(behavior, store, ctx, now=ko - timedelta(hours=3))
            self.assertFalse(d_skip.should_post)

            # 2) Lineup announced 60m before KO -> barca_lineup
            m_pre.home_lineup = _xi("Barcelona")
            d2 = evaluate_workflow(behavior, store, ctx, now=ko - timedelta(minutes=60))
            self.assertTrue(d2.should_post)
            self.assertEqual(d2.workflow_slot, "barca_lineup")
            store.mark_match_event_sent(*d2.event_lock, created_at_utc=ko - timedelta(minutes=60))

            # 3) Half-time drawing 0-0 -> barca_halftime meltdown
            m_ht = MatchInfo(
                competition="La Liga", league_slug="esp.1", home_team="Barcelona", away_team="Valencia",
                home_score=0, away_score=0, status_state="in", status_detail="HT",
                kickoff_utc=ko, event_id="1001", status_type_name="STATUS_HALFTIME",
            )
            ctx_ht = LiveFootballContext(fetched_at_utc=ko + timedelta(minutes=48), match_state=MatchState.BARCA_HALFTIME_STRUGGLING, barca_matches=[m_ht])
            d3 = evaluate_workflow(behavior, store, ctx_ht, now=ko + timedelta(minutes=48))
            self.assertTrue(d3.should_post)
            self.assertEqual(d3.workflow_slot, "barca_halftime")
            self.assertEqual(d3.archetype_name, "post_drop_meltdown")
            store.mark_match_event_sent(*d3.event_lock, created_at_utc=ko + timedelta(minutes=48))

            # 4a) If Barca DREW 1-1 -> exactly 3 FT frustration posts spaced 18m+ apart (total = 6!)
            m_draw = MatchInfo(
                competition="La Liga", league_slug="esp.1", home_team="Barcelona", away_team="Valencia",
                home_score=1, away_score=1, status_state="post", status_detail="FT",
                kickoff_utc=ko, event_id="1001",
            )
            ft_t0 = ko + timedelta(minutes=115)
            ctx_ft = LiveFootballContext(fetched_at_utc=ft_t0, match_state=MatchState.BARCA_POST_DROP, barca_matches=[m_draw])
            d4 = evaluate_workflow(behavior, store, ctx_ft, now=ft_t0)
            self.assertTrue(d4.should_post)
            self.assertEqual(d4.workflow_slot, "barca_fulltime_1")
            store.mark_match_event_sent(*d4.event_lock, created_at_utc=ft_t0)

            # 10m later -> wait
            d4_wait = evaluate_workflow(behavior, store, ctx_ft, now=ft_t0 + timedelta(minutes=10))
            self.assertFalse(d4_wait.should_post)

            # 20m later -> barca_fulltime_2
            d5 = evaluate_workflow(behavior, store, ctx_ft, now=ft_t0 + timedelta(minutes=20))
            self.assertTrue(d5.should_post)
            self.assertEqual(d5.workflow_slot, "barca_fulltime_2")
            store.mark_match_event_sent(*d5.event_lock, created_at_utc=ft_t0 + timedelta(minutes=20))

            # 40m later -> barca_fulltime_3
            d6 = evaluate_workflow(behavior, store, ctx_ft, now=ft_t0 + timedelta(minutes=40))
            self.assertTrue(d6.should_post)
            self.assertEqual(d6.workflow_slot, "barca_fulltime_3")
            store.mark_match_event_sent(*d6.event_lock, created_at_utc=ft_t0 + timedelta(minutes=40))

            # 60m later -> NO 7th post!
            d7 = evaluate_workflow(behavior, store, ctx_ft, now=ft_t0 + timedelta(minutes=60))
            self.assertFalse(d7.should_post)

    def test_madrid_matchday_exact_3_posts(self):
        with TemporaryDirectory() as tmp:
            settings = Settings(sqlite_path=Path(tmp) / "history.db")
            store = HistoryStore(settings)
            behavior = settings.default_behavior()
            ko = datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc)

            # 1) Madrid lineup announced -> madrid_lineup
            m_pre = MatchInfo(
                competition="La Liga", league_slug="esp.1", home_team="Real Madrid", away_team="Sevilla",
                home_score=None, away_score=None, status_state="pre", status_detail="20:00 UTC",
                kickoff_utc=ko, event_id="2002", home_lineup=_xi("Real Madrid"),
            )
            ctx = LiveFootballContext(fetched_at_utc=ko - timedelta(minutes=55), match_state=MatchState.OFF_DAY_BANTER, madrid_matches=[m_pre])
            d1 = evaluate_workflow(behavior, store, ctx, now=ko - timedelta(minutes=55))
            self.assertTrue(d1.should_post)
            self.assertEqual(d1.workflow_slot, "madrid_lineup")
            self.assertEqual(d1.target_club, "Real Madrid")
            store.mark_match_event_sent(*d1.event_lock, created_at_utc=ko - timedelta(minutes=55))

            # 2) Madrid won 2-0 -> exactly 2 FT discrediting posts
            m_ft = MatchInfo(
                competition="La Liga", league_slug="esp.1", home_team="Real Madrid", away_team="Sevilla",
                home_score=2, away_score=0, status_state="post", status_detail="FT",
                kickoff_utc=ko, event_id="2002",
            )
            ft0 = ko + timedelta(minutes=115)
            ctx_ft = LiveFootballContext(fetched_at_utc=ft0, match_state=MatchState.MADRID_POST_WIN, madrid_matches=[m_ft])
            d2 = evaluate_workflow(behavior, store, ctx_ft, now=ft0)
            self.assertTrue(d2.should_post)
            self.assertEqual(d2.workflow_slot, "madrid_fulltime_1")
            self.assertIn("DISCREDITING", d2.slot_instructions)
            store.mark_match_event_sent(*d2.event_lock, created_at_utc=ft0)

            d3 = evaluate_workflow(behavior, store, ctx_ft, now=ft0 + timedelta(minutes=25))
            self.assertTrue(d3.should_post)
            self.assertEqual(d3.workflow_slot, "madrid_fulltime_2")
            store.mark_match_event_sent(*d3.event_lock, created_at_utc=ft0 + timedelta(minutes=25))

            # No 4th post!
            d4 = evaluate_workflow(behavior, store, ctx_ft, now=ft0 + timedelta(minutes=50))
            self.assertFalse(d4.should_post)

    def test_thursday_ucl_epl_roast_and_offday_weekly_throttling(self):
        with TemporaryDirectory() as tmp:
            settings = Settings(sqlite_path=Path(tmp) / "history.db")
            store = HistoryStore(settings)
            behavior = settings.default_behavior()

            # Thursday Nov 26, 2026 07:00 UTC = 12:30 PM IST
            thu_now = datetime(2026, 11, 26, 7, 0, tzinfo=timezone.utc)
            epl_loss = MatchInfo(
                competition="UEFA Champions League", league_slug="uefa.champions",
                home_team="Galatasaray", away_team="Aston Villa",
                home_score=2, away_score=1, status_state="post", status_detail="FT",
                kickoff_utc=thu_now - timedelta(hours=36), event_id="3003",
            )
            self.assertEqual(epl_loss.epl_lost_in_ucl(), "Aston Villa")
            ctx_thu = LiveFootballContext(
                fetched_at_utc=thu_now, match_state=MatchState.OFF_DAY_BANTER, ucl_recent_epl_losses=[epl_loss]
            )
            d_thu = evaluate_workflow(behavior, store, ctx_thu, now=thu_now)
            self.assertTrue(d_thu.should_post)
            self.assertEqual(d_thu.workflow_slot, "thursday_ucl_epl")
            self.assertEqual(d_thu.target_club, "Aston Villa")
            store.mark_match_event_sent(*d_thu.event_lock, created_at_utc=thu_now)

            # Big-8 off-day rotation NEVER includes Real Madrid
            clubs = pick_daily_big_eight_clubs(thu_now, ["Arsenal", "Chelsea"], count=2)
            self.assertEqual(len(clubs), 2)
            for c in clubs:
                self.assertIn(c, BIG_EIGHT_RIVALS)
                self.assertNotEqual(c, "Real Madrid")

            # Seed 25 posts over last 6 days -> weekly throttle drops non-matchday quota to 1/day!
            for i in range(25):
                store.record_post(
                    f"tw-{i}", "epl_intensity_slander", "off_day_banter", "angle", f"post {i}", [],
                    dry_run=False, created_at_utc=thu_now - timedelta(days=1, hours=i % 12), target_club="Arsenal",
                )
            # Friday 06:00 UTC -> 1st off-day post allowed
            fri_6 = datetime(2026, 11, 27, 6, 0, tzinfo=timezone.utc)
            ctx_off = LiveFootballContext(fetched_at_utc=fri_6, match_state=MatchState.OFF_DAY_BANTER)
            d_fri1 = evaluate_workflow(behavior, store, ctx_off, now=fri_6)
            self.assertTrue(d_fri1.should_post)
            self.assertIn(d_fri1.target_club, BIG_EIGHT_RIVALS)
            store.record_post(
                "tw-fri1", d_fri1.archetype_name, "off_day_banter", "angle", "fri post", [],
                dry_run=False, created_at_utc=fri_6, target_club=d_fri1.target_club,
            )

            # Friday 11:00 UTC -> throttled to 1/day because 7-day rolling total >= 25!
            fri_11 = datetime(2026, 11, 27, 11, 0, tzinfo=timezone.utc)
            d_fri2 = evaluate_workflow(behavior, store, ctx_off, now=fri_11)
            self.assertFalse(d_fri2.should_post)
            self.assertIn("Off-day quota complete (1/1", d_fri2.reason)


class TestOtherCoreModules(unittest.TestCase):
    def test_parse_espn_and_laliga_beat_epl(self):
        payload = {
            "events": [
                {
                    "id": "777888",
                    "date": "2026-11-25T20:00Z",
                    "competitions": [
                        {
                            "date": "2026-11-25T20:00Z",
                            "status": {"type": {"state": "post", "name": "STATUS_FULL_TIME", "shortDetail": "FT"}},
                            "competitors": [
                                {"homeAway": "home", "score": "2", "team": {"displayName": "Athletic Club"}},
                                {"homeAway": "away", "score": "0", "team": {"displayName": "Arsenal"}},
                            ],
                        }
                    ],
                }
            ]
        }
        matches = parse_espn_events(payload, "UEFA Champions League", league_slug="uefa.champions")
        self.assertTrue(matches[0].laliga_beat_epl())
        self.assertEqual(matches[0].epl_lost_in_ucl(), "Arsenal")

    def test_dual_key_failover_and_validation(self):
        _, errs = sanitize_and_validate_text("Great goal #Barca")
        self.assertTrue(any("hashtag" in e.lower() for e in errs))
        self.assertTrue(is_too_similar("Pedri is clear of everyone", ["Pedri is clear of everyone today"]))

        with TemporaryDirectory() as tmp:
            assets_dir = Path(tmp) / "images"
            (assets_dir / "barca").mkdir(parents=True)
            Image.new("RGB", (200, 200), (165, 0, 68)).save(assets_dir / "barca" / "pedri_serious.jpg")
            catalog = ImageCatalog(assets_dir, Path(tmp) / "catalog.json")

            valid_payload = GeneratedPost(
                text="Intensity League teams are trash.\n\nEnd.",
                selected_image_ids=["barca_pedri_serious"],
                angle_summary="Mocking EPL intensity league",
            ).model_dump_json()

            primary_mock = MagicMock()
            primary_mock.interactions.create.side_effect = RuntimeError("429 quota exceeded")
            fallback_mock = MagicMock()
            fallback_mock.interactions.create.return_value = SimpleNamespace(output_text=valid_payload)

            settings = Settings(gemini_api_key="k1", gemini_api_key_fallback="k2", gemini_model="gemini-3.5-flash-lite", enable_google_search=False)
            gen = PostGenerator(settings, primary_client=primary_mock, fallback_client=fallback_mock)
            ctx = LiveFootballContext(fetched_at_utc=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc), match_state=MatchState.OFF_DAY_BANTER)
            res = gen.generate(ctx, catalog, [], [], target_club="Arsenal", slot_instructions="Roast Arsenal")
            self.assertEqual(res.gemini_key_used, "fallback")

    def test_x_v2_chunked_upload_and_config_isolation(self):
        with TemporaryDirectory() as tmp:
            img_path = Path(tmp) / "yamal.jpg"
            Image.new("RGB", (300, 300), (0, 77, 152)).save(img_path, format="JPEG")
            raw_bytes, mime, _ = prepare_image_for_x(img_path)
            self.assertEqual(mime, "image/jpeg")
            self.assertGreater(len(raw_bytes), 100)

            mock_session = MagicMock()
            mock_session.post.side_effect = [
                MagicMock(status_code=200, json=lambda: {"data": {"id": "999111"}}),
                MagicMock(status_code=204),
                MagicMock(status_code=200),
                MagicMock(status_code=201, json=lambda: {"data": {"id": "1234567890"}}),
            ]
            client = XClient(Settings(x_api_key="k", x_api_secret="s", x_access_token="t", x_access_token_secret="ts"), session=mock_session)
            result = client.create_post("Test", image_paths=[img_path])
            self.assertEqual(result.tweet_id, "1234567890")

            with patch.dict("os.environ", {"GEMINI_API_KEY": "global-secret", "BARCA_GEMINI_API_KEY": "", "DYNO": ""}, clear=False):
                self.assertEqual(load_settings(env_file=Path(tmp) / "missing.env").gemini_api_key, "")


if __name__ == "__main__":
    unittest.main()
