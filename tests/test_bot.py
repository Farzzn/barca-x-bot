"""Automated tests for barca-x-bot (HT/FT triggers, dual Gemini keys, DB settings, GitHub images, X v2)."""

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
    LiveFootballContext,
    MatchInfo,
    MatchState,
    detect_match_state,
    parse_espn_events,
)
from app.generator import GeneratedPost, PostGenerator, is_too_similar, sanitize_and_validate_text
from app.image_catalog import ImageCatalog, infer_metadata_from_path, prepare_image_for_x
from app.storage import HistoryStore, decide_run_action, should_post_now
from app.x_client import XClient


class TestMatchContext(unittest.TestCase):
    def test_pre_match_halftime_and_post_drop_detection(self):
        now = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)
        upcoming = MatchInfo(
            competition="UEFA Champions League",
            home_team="Barcelona",
            away_team="Napoli",
            home_score=None,
            away_score=None,
            status_state="pre",
            status_detail="20:00 UTC",
            kickoff_utc=now + timedelta(hours=2),
            event_id="9001",
        )
        state, focus = detect_match_state([upcoming], [], now)
        self.assertEqual(state, MatchState.BARCA_PRE_MATCH)
        self.assertEqual(focus, upcoming)

        ht_draw = MatchInfo(
            competition="UEFA Champions League",
            home_team="Barcelona",
            away_team="Napoli",
            home_score=0,
            away_score=0,
            status_state="in",
            status_detail="HT",
            kickoff_utc=now - timedelta(minutes=50),
            event_id="9001",
            status_type_name="STATUS_HALFTIME",
        )
        state, focus = detect_match_state([ht_draw], [], now)
        self.assertEqual(state, MatchState.BARCA_HALFTIME_STRUGGLING)
        self.assertTrue(focus.is_halftime)

        ht_win = MatchInfo(
            competition="UEFA Champions League",
            home_team="Barcelona",
            away_team="Napoli",
            home_score=2,
            away_score=0,
            status_state="in",
            status_detail="HT",
            kickoff_utc=now - timedelta(minutes=50),
            event_id="9001",
            status_type_name="STATUS_HALFTIME",
        )
        state, _ = detect_match_state([ht_win], [], now)
        self.assertEqual(state, MatchState.BARCA_HALFTIME_WINNING)

        drew = MatchInfo(
            competition="UEFA Champions League",
            home_team="Barcelona",
            away_team="Napoli",
            home_score=1,
            away_score=1,
            status_state="post",
            status_detail="FT",
            kickoff_utc=now - timedelta(hours=3),
            event_id="9001",
        )
        state, focus = detect_match_state([drew], [], now)
        self.assertEqual(state, MatchState.BARCA_POST_DROP)
        self.assertEqual(focus, drew)

    def test_parse_espn_events(self):
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
                                {"homeAway": "home", "score": "3", "team": {"displayName": "Barcelona"}},
                                {"homeAway": "away", "score": "1", "team": {"displayName": "Atletico Madrid"}},
                            ],
                            "details": [
                                {
                                    "scoringPlay": True,
                                    "clock": {"displayValue": "22'"},
                                    "athletesInvolved": [{"displayName": "Lamine Yamal"}],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        matches = parse_espn_events(payload, "La Liga")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].match_key, "espn:777888")
        self.assertTrue(matches[0].involves_barca)
        self.assertEqual(matches[0].barca_outcome(), "win")
        self.assertEqual(matches[0].goal_scorers, ["Lamine Yamal (22')"])

    def test_laliga_beat_epl_and_epl_europe_flop_detection(self):
        now = datetime(2026, 11, 25, 22, 0, tzinfo=timezone.utc)
        laliga_win = MatchInfo(
            competition="UEFA Champions League",
            home_team="Athletic Club",
            away_team="Arsenal",
            home_score=2,
            away_score=0,
            status_state="post",
            status_detail="FT",
            kickoff_utc=now - timedelta(hours=2),
        )
        self.assertTrue(laliga_win.laliga_beat_epl())
        self.assertTrue(laliga_win.epl_dropped_in_europe())

        city_draw = MatchInfo(
            competition="UEFA Champions League",
            home_team="Manchester City",
            away_team="Napoli",
            home_score=2,
            away_score=2,
            status_state="post",
            status_detail="FT",
            kickoff_utc=now - timedelta(hours=3),
        )
        self.assertFalse(city_draw.laliga_beat_epl())
        self.assertTrue(city_draw.epl_dropped_in_europe())


class TestImageCatalog(unittest.TestCase):
    def test_metadata_inference_and_prepare_for_x(self):
        asset = infer_metadata_from_path(Path("rivals/vinicius_complaining.webp"))
        self.assertEqual(asset.id, "rivals_vinicius_complaining")
        self.assertEqual(asset.club, "Real Madrid")
        self.assertEqual(asset.subject, "Vinicius")
        self.assertEqual(asset.mood, "complaining")

        with TemporaryDirectory() as tmp:
            assets_dir = Path(tmp) / "images"
            (assets_dir / "barca").mkdir(parents=True)
            (assets_dir / "rivals").mkdir(parents=True)

            img1_path = assets_dir / "barca" / "lamine_yamal_celebrating.png"
            img2_path = assets_dir / "rivals" / "mbappe_shocked.webp"
            Image.new("RGBA", (600, 600), (0, 77, 152, 255)).save(img1_path)
            Image.new("RGB", (600, 600), (255, 255, 255)).save(img2_path, format="WEBP")

            catalog = ImageCatalog(assets_dir, Path(tmp) / "catalog.json")
            self.assertEqual(len(catalog.assets), 2)
            raw_bytes, mime, filename = prepare_image_for_x(img2_path)
            self.assertEqual(mime, "image/jpeg")
            self.assertTrue(filename.endswith(".jpg"))
            self.assertGreater(len(raw_bytes), 100)


class TestDualKeyGenerator(unittest.TestCase):
    def test_validation_and_similarity(self):
        _, errs = sanitize_and_validate_text("Great goal #Barca")
        self.assertTrue(any("hashtag" in e.lower() for e in errs))
        _, errs = sanitize_and_validate_text("Check this https://example.com")
        self.assertTrue(any("url" in e.lower() for e in errs))
        _, errs = sanitize_and_validate_text("a" * 285)
        self.assertTrue(any("exceeds" in e.lower() for e in errs))
        clean, errs = sanitize_and_validate_text("Pedri is clear.\n\nEnd.")
        self.assertEqual(errs, [])
        self.assertEqual(clean, "Pedri is clear.\n\nEnd.")

        self.assertTrue(
            is_too_similar(
                "Pedri is clear of Bellingham and it is not even close",
                ["Pedri is clear of Bellingham and it is not close at all"],
            )
        )

    def test_failover_from_primary_key_to_fallback_key(self):
        with TemporaryDirectory() as tmp:
            assets_dir = Path(tmp) / "images"
            (assets_dir / "barca").mkdir(parents=True)
            Image.new("RGB", (200, 200), (165, 0, 68)).save(assets_dir / "barca" / "pedri_serious.jpg")
            catalog = ImageCatalog(assets_dir, Path(tmp) / "catalog.json")

            valid_payload = GeneratedPost(
                text="I'll never understand why Madrid fans compare anyone to Pedri.\n\nPedri is clear.\n\nEnd.",
                selected_image_ids=["barca_pedri_serious", "fake_id"],
                angle_summary="Pedri supremacy over Madrid midfielders",
            ).model_dump_json()

            primary_mock = MagicMock()
            primary_mock.interactions.create.side_effect = RuntimeError("429 quota exceeded on primary key")
            fallback_mock = MagicMock()
            fallback_mock.interactions.create.return_value = SimpleNamespace(output_text=valid_payload)

            settings = Settings(
                gemini_api_key="key1",
                gemini_api_key_fallback="key2",
                gemini_model="gemini-3.5-flash-lite",
                enable_google_search=False,
            )
            gen = PostGenerator(settings, primary_client=primary_mock, fallback_client=fallback_mock)
            ctx = LiveFootballContext(
                fetched_at_utc=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc),
                match_state=MatchState.OFF_DAY_BANTER,
            )

            result = gen.generate(ctx, catalog, recent_posts=[], recent_archetypes=[])
            self.assertEqual(result.gemini_key_used, "fallback")
            self.assertIn("429 quota exceeded", result.gemini_fallback_reason)
            self.assertEqual(result.gemini_model_used, "gemini-3.5-flash-lite")
            self.assertEqual(result.post.selected_image_ids, ["barca_pedri_serious"])


class TestStorageTriggersAndSettings(unittest.TestCase):
    def test_halftime_and_fulltime_exact_once_and_admin_pause(self):
        with TemporaryDirectory() as tmp:
            settings = Settings(
                max_posts_per_day=2,
                min_minutes_between_posts=120,
                matchday_cooldown_minutes=45,
                post_hours_utc=[10, 14, 18],
                sqlite_path=Path(tmp) / "history.db",
            )
            store = HistoryStore(settings)
            behavior = store.get_behavior_config(settings.default_behavior())

            now = datetime(2026, 11, 25, 20, 48, tzinfo=timezone.utc)
            ht_match = MatchInfo(
                competition="UEFA Champions League",
                home_team="Barcelona",
                away_team="Sabah FK",
                home_score=0,
                away_score=1,
                status_state="in",
                status_detail="HT",
                kickoff_utc=now - timedelta(minutes=48),
                event_id="555111",
                status_type_name="STATUS_HALFTIME",
            )
            ht_ctx = LiveFootballContext(
                fetched_at_utc=now,
                match_state=MatchState.BARCA_HALFTIME_STRUGGLING,
                focus_match=ht_match,
            )

            # Even at UTC hour 20 (not in [10,14,18]), Half-Time fires immediately!
            ok, trigger, _ = decide_run_action(behavior, store, ht_ctx, now=now)
            self.assertTrue(ok)
            self.assertEqual(trigger, "halftime")

            # Mark HT sent and record post -> next tick 10 mins later respects matchday cooldown
            store.mark_match_event_sent(ht_match.match_key, "halftime")
            store.record_post(
                "t1", "post_drop_meltdown", ht_ctx.match_state.value, "ht rage", "SACK HIM", [],
                dry_run=False, created_at_utc=now, trigger_type="halftime", gemini_key_used="primary",
            )
            ok2, trigger2, _ = decide_run_action(behavior, store, ht_ctx, now=now + timedelta(minutes=5))
            self.assertFalse(ok2)
            self.assertEqual(trigger2, "cooldown")

            # Full-time fires immediately even inside regular cooldown!
            ft_now = now + timedelta(minutes=60)
            ft_match = MatchInfo(
                competition="UEFA Champions League",
                home_team="Barcelona",
                away_team="Sabah FK",
                home_score=1,
                away_score=1,
                status_state="post",
                status_detail="FT",
                kickoff_utc=now - timedelta(minutes=48),
                event_id="555111",
                status_type_name="STATUS_FULL_TIME",
            )
            ft_ctx = LiveFootballContext(
                fetched_at_utc=ft_now,
                match_state=MatchState.BARCA_POST_DROP,
                focus_match=ft_match,
            )
            ok3, trigger3, _ = decide_run_action(behavior, store, ft_ctx, now=ft_now)
            self.assertTrue(ok3)
            self.assertEqual(trigger3, "fulltime")

            # Pausing bot from Admin UI blocks everything cleanly
            behavior.bot_enabled = False
            store.save_behavior_config(behavior)
            reloaded = store.get_behavior_config(settings.default_behavior())
            ok4, trigger4, _ = decide_run_action(reloaded, store, ft_ctx, now=ft_now)
            self.assertFalse(ok4)
            self.assertEqual(trigger4, "paused")

    def test_backwards_compatible_should_post_now(self):
        with TemporaryDirectory() as tmp:
            settings = Settings(post_hours_utc=[10], sqlite_path=Path(tmp) / "history.db")
            store = HistoryStore(settings)
            ctx = LiveFootballContext(
                fetched_at_utc=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
                match_state=MatchState.OFF_DAY_BANTER,
            )
            ok, _ = should_post_now(settings, store, ctx, now=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc))
            self.assertTrue(ok)


class TestXClientV2(unittest.TestCase):
    def test_v2_chunked_upload_and_tweet_post(self):
        with TemporaryDirectory() as tmp:
            img_path = Path(tmp) / "yamal.jpg"
            Image.new("RGB", (300, 300), (0, 77, 152)).save(img_path, format="JPEG")

            mock_session = MagicMock()
            init_resp = MagicMock(status_code=200)
            init_resp.json.return_value = {"data": {"id": "999111"}}
            append_resp = MagicMock(status_code=204)
            finalize_resp = MagicMock(status_code=200)
            tweet_resp = MagicMock(status_code=201)
            tweet_resp.json.return_value = {"data": {"id": "1234567890"}}

            mock_session.post.side_effect = [init_resp, append_resp, finalize_resp, tweet_resp]

            settings = Settings(
                x_api_key="k",
                x_api_secret="s",
                x_access_token="t",
                x_access_token_secret="ts",
            )
            client = XClient(settings, session=mock_session)
            result = client.create_post("LAMINE YOU ARE HIM!!!", image_paths=[img_path])

            self.assertEqual(result.tweet_id, "1234567890")
            self.assertEqual(result.media_ids, ["999111"])


class TestConfigIsolation(unittest.TestCase):
    def test_ignores_global_gemini_api_key_locally_and_loads_fallback(self):
        with TemporaryDirectory() as tmp:
            missing_env = Path(tmp) / ".env"
            with patch.dict(
                "os.environ",
                {"GEMINI_API_KEY": "global-secret", "BARCA_GEMINI_API_KEY": "", "BARCA_GEMINI_API_KEY_2": "", "DYNO": ""},
                clear=False,
            ):
                settings = load_settings(env_file=missing_env)
                self.assertEqual(settings.gemini_api_key, "")

            local_env = Path(tmp) / ".env"
            local_env.write_text(
                "BARCA_GEMINI_API_KEY=key-one\nBARCA_GEMINI_API_KEY_2=key-two\n",
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"GEMINI_API_KEY": "global-secret", "BARCA_GEMINI_API_KEY": "", "BARCA_GEMINI_API_KEY_2": "", "DYNO": ""},
                clear=False,
            ):
                settings = load_settings(env_file=local_env)
                self.assertEqual(settings.gemini_api_key, "key-one")
                self.assertEqual(settings.gemini_api_key_fallback, "key-two")


if __name__ == "__main__":
    unittest.main()
