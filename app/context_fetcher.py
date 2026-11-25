"""Live FC Barcelona & rival match/news context fetcher.

Uses free public ESPN soccer scoreboards + football RSS + Google News post-match RSS
to detect exact high-engagement triggers:
  - BARCA_HALFTIME_WINNING / BARCA_HALFTIME_STRUGGLING (instant HT reaction)
  - BARCA_POST_WIN / BARCA_POST_DROP (instant FT reaction + post-match news pull)
  - BARCA_PRE_MATCH (delusional pre-match hype)
  - BARCA_LIVE_WINNING / BARCA_LIVE_STRUGGLING (in-match swings)
  - RIVAL_MATCH_DRAMA / OFF_DAY_BANTER
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 10
USER_AGENT = "BarcaXBot/1.1 (+https://github.com/local/barca-x-bot)"

ESPN_LEAGUES = [
    ("esp.1", "La Liga"),
    ("uefa.champions", "UEFA Champions League"),
    ("uefa.europa", "UEFA Europa League"),
    ("eng.1", "Premier League"),
    ("esp.copa_del_rey", "Copa del Rey"),
    ("esp.super_cup", "Supercopa de Espana"),
]

RSS_FEEDS = [
    ("Barca Blaugranes", "https://www.barcablaugranes.com/rss/current.xml"),
    ("Managing Madrid", "https://www.managingmadrid.com/rss/current.xml"),
    ("BBC Football", "https://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("ESPN FC", "https://www.espn.com/espn/rss/soccer/news"),
]

BARCA_ALIASES = {"barcelona", "fc barcelona", "barca"}
LALIGA_CLUBS = {
    "barcelona",
    "fc barcelona",
    "real madrid",
    "atletico madrid",
    "atlético madrid",
    "athletic club",
    "athletic bilbao",
    "real sociedad",
    "villarreal",
    "real betis",
    "sevilla",
    "girona",
    "valencia",
    "celta vigo",
    "osasuna",
    "rayo vallecano",
}
EPL_CLUBS = {
    "manchester city",
    "arsenal",
    "liverpool",
    "chelsea",
    "manchester united",
    "tottenham",
    "aston villa",
    "newcastle",
    "nottingham forest",
    "brighton",
    "west ham",
}
RIVAL_ALIASES = {
    "real madrid",
    "atletico madrid",
    "atlético madrid",
    "manchester city",
    "arsenal",
    "liverpool",
    "chelsea",
    "manchester united",
    "tottenham",
    "aston villa",
    "newcastle",
    "paris saint-germain",
    "psg",
    "bayern munich",
    "bayern",
    "espanyol",
}


class MatchState(str, Enum):
    """High-level football mood state driving the persona's emotional swings."""

    BARCA_PRE_MATCH = "barca_pre_match"
    BARCA_HALFTIME_WINNING = "barca_halftime_winning"
    BARCA_HALFTIME_STRUGGLING = "barca_halftime_struggling"
    BARCA_LIVE_WINNING = "barca_live_winning"
    BARCA_LIVE_STRUGGLING = "barca_live_struggling"
    BARCA_POST_WIN = "barca_post_win"
    BARCA_POST_DROP = "barca_post_drop"
    RIVAL_MATCH_DRAMA = "rival_match_drama"
    OFF_DAY_BANTER = "off_day_banter"


@dataclass
class MatchInfo:
    competition: str
    home_team: str
    away_team: str
    home_score: Optional[int]
    away_score: Optional[int]
    status_state: str  # "pre", "in", "post"
    status_detail: str
    kickoff_utc: datetime
    goal_scorers: List[str] = field(default_factory=list)
    event_id: str = ""
    status_type_name: str = ""

    @property
    def match_key(self) -> str:
        if self.event_id:
            return f"espn:{self.event_id}"
        slug = re.sub(r"[^a-z0-9]+", "_", f"{self.home_team}_{self.away_team}".lower()).strip("_")
        return f"{slug}:{self.kickoff_utc.strftime('%Y%m%d')}"

    @property
    def is_halftime(self) -> bool:
        if self.status_state != "in":
            return False
        name = self.status_type_name.upper()
        detail = self.status_detail.strip().upper()
        return (
            "HALFTIME" in name
            or detail == "HT"
            or detail.startswith("HT ")
            or "HALF TIME" in detail
            or "HALFTIME" in detail
        )

    @property
    def is_fulltime(self) -> bool:
        return self.status_state == "post"

    @property
    def involves_barca(self) -> bool:
        return self._is_barca(self.home_team) or self._is_barca(self.away_team)

    @property
    def involves_rival(self) -> bool:
        return self._is_rival(self.home_team) or self._is_rival(self.away_team)

    @property
    def involves_epl(self) -> bool:
        return self._is_epl(self.home_team) or self._is_epl(self.away_team)

    @property
    def involves_laliga(self) -> bool:
        return self._is_laliga(self.home_team) or self._is_laliga(self.away_team)

    @staticmethod
    def _is_barca(name: str) -> bool:
        low = name.lower()
        return any(alias in low for alias in BARCA_ALIASES)

    @staticmethod
    def _is_rival(name: str) -> bool:
        low = name.lower()
        return any(alias in low for alias in RIVAL_ALIASES)

    @staticmethod
    def _is_epl(name: str) -> bool:
        low = name.lower()
        return any(club in low for club in EPL_CLUBS)

    @staticmethod
    def _is_laliga(name: str) -> bool:
        low = name.lower()
        return any(club in low for club in LALIGA_CLUBS)

    def laliga_beat_epl(self) -> bool:
        """True if a La Liga side beat (or is currently beating) an EPL club."""
        if not (self.involves_laliga and self.involves_epl):
            return False
        if self.home_score is None or self.away_score is None:
            return False
        if self._is_laliga(self.home_team) and self._is_epl(self.away_team):
            return self.home_score > self.away_score
        if self._is_laliga(self.away_team) and self._is_epl(self.home_team):
            return self.away_score > self.home_score
        return False

    def epl_dropped_in_europe(self) -> bool:
        """True if an EPL club drew or lost in UCL/UEL."""
        if "UEFA" not in self.competition or not self.involves_epl:
            return False
        if self.home_score is None or self.away_score is None:
            return False
        if self._is_epl(self.home_team) and not self._is_epl(self.away_team):
            return self.home_score <= self.away_score
        if self._is_epl(self.away_team) and not self._is_epl(self.home_team):
            return self.away_score <= self.home_score
        return False

    def barca_outcome(self) -> Optional[str]:
        """Return 'win', 'draw', or 'loss' from Barca's perspective if scores exist."""
        if not self.involves_barca or self.home_score is None or self.away_score is None:
            return None
        if self._is_barca(self.home_team):
            barca_goals, opp_goals = self.home_score, self.away_score
        else:
            barca_goals, opp_goals = self.away_score, self.home_score
        if barca_goals > opp_goals:
            return "win"
        if barca_goals == opp_goals:
            return "draw"
        return "loss"

    def opponent_of_barca(self) -> Optional[str]:
        if not self.involves_barca:
            return None
        return self.away_team if self._is_barca(self.home_team) else self.home_team

    def rival_dropped_points(self) -> bool:
        """True if a tracked rival drew or lost (in-progress or finished)."""
        if not self.involves_rival or self.involves_barca:
            return False
        if self.home_score is None or self.away_score is None:
            return False
        if self._is_rival(self.home_team):
            return self.home_score <= self.away_score
        if self._is_rival(self.away_team):
            return self.away_score <= self.home_score
        return False

    def summary_line(self) -> str:
        kickoff_str = self.kickoff_utc.strftime("%Y-%m-%d %H:%M UTC")
        if self.status_state == "pre":
            return f"[{self.competition}] {self.home_team} vs {self.away_team} — Kickoff {kickoff_str} ({self.status_detail})"
        score = f"{self.home_score}-{self.away_score}" if self.home_score is not None else "vs"
        scorers = f" | Scorers: {', '.join(self.goal_scorers[:6])}" if self.goal_scorers else ""
        return f"[{self.competition}] {self.home_team} {score} {self.away_team} ({self.status_detail}){scorers}"


@dataclass
class LiveFootballContext:
    fetched_at_utc: datetime
    match_state: MatchState
    focus_match: Optional[MatchInfo] = None
    barca_matches: List[MatchInfo] = field(default_factory=list)
    rival_matches: List[MatchInfo] = field(default_factory=list)
    headlines: List[str] = field(default_factory=list)
    post_match_news: List[str] = field(default_factory=list)
    epl_slander_alerts: List[str] = field(default_factory=list)

    @property
    def has_laliga_beat_epl(self) -> bool:
        return any(a.startswith("LA LIGA OWNAGE") for a in self.epl_slander_alerts)

    @property
    def is_matchday_hot(self) -> bool:
        return self.match_state in {
            MatchState.BARCA_PRE_MATCH,
            MatchState.BARCA_HALFTIME_WINNING,
            MatchState.BARCA_HALFTIME_STRUGGLING,
            MatchState.BARCA_LIVE_WINNING,
            MatchState.BARCA_LIVE_STRUGGLING,
            MatchState.BARCA_POST_WIN,
            MatchState.BARCA_POST_DROP,
        }

    @property
    def instant_event_type(self) -> Optional[str]:
        """Return 'halftime' or 'fulltime' when focus_match is a fresh instant trigger."""
        if not self.focus_match or not self.focus_match.involves_barca:
            return None
        if self.match_state in {MatchState.BARCA_HALFTIME_WINNING, MatchState.BARCA_HALFTIME_STRUGGLING}:
            return "halftime"
        if self.match_state in {MatchState.BARCA_POST_WIN, MatchState.BARCA_POST_DROP}:
            # Full-time instant trigger window: within 6 hours of kickoff (match ends ~2h after kickoff)
            age = self.fetched_at_utc - self.focus_match.kickoff_utc
            if timedelta(0) <= age <= timedelta(hours=6):
                return "fulltime"
        return None

    def to_prompt_block(self) -> str:
        lines = [
            f"Current UTC time: {self.fetched_at_utc.strftime('%Y-%m-%d %H:%M UTC')}",
            f"Detected Match State: {self.match_state.value}",
        ]
        if self.focus_match:
            lines.append(f"Primary Focus Match: {self.focus_match.summary_line()}")
        if self.barca_matches:
            lines.append("Recent & Upcoming FC Barcelona Matches:")
            for m in self.barca_matches[:4]:
                lines.append(f"  - {m.summary_line()}")
        if self.rival_matches:
            lines.append("Recent & Upcoming Rival Matches (Real Madrid / Atleti / EPL 'Intensity League' / UCL):")
            for m in self.rival_matches[:6]:
                lines.append(f"  - {m.summary_line()}")
        if self.epl_slander_alerts:
            lines.append("EPL 'INTENSITY LEAGUE' SLANDER ALERTS (PRIME AMMO FOR MOCKING THE PREMIER LEAGUE):")
            for alert in self.epl_slander_alerts[:5]:
                lines.append(f"  - {alert}")
        if self.post_match_news:
            lines.append("Breaking Live / Post-Match FC Barcelona News:")
            for item in self.post_match_news[:6]:
                lines.append(f"  - {item}")
        if self.headlines:
            lines.append("Latest Football Headlines:")
            for h in self.headlines[:10]:
                lines.append(f"  - {h}")
        if not self.barca_matches and not self.rival_matches and not self.headlines:
            lines.append("No live scoreboard/RSS items fetched; rely on Google Search grounding if enabled.")
        return "\n".join(lines)


def _parse_espn_datetime(raw: str) -> datetime:
    cleaned = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_espn_events(payload: Dict[str, Any], competition: str) -> List[MatchInfo]:
    """Parse an ESPN scoreboard JSON payload into structured MatchInfo objects."""
    matches: List[MatchInfo] = []
    for event in payload.get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        comp = competitions[0]
        competitors = comp.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        home_name = (home.get("team") or {}).get("displayName", "Unknown")
        away_name = (away.get("team") or {}).get("displayName", "Unknown")

        status_obj = comp.get("status") or event.get("status") or {}
        status_type = status_obj.get("type") or {}
        state = str(status_type.get("state", "pre")).lower()
        type_name = str(status_type.get("name", ""))
        detail = str(status_type.get("shortDetail") or status_type.get("description") or state)

        raw_date = comp.get("date") or event.get("date")
        if not raw_date:
            continue
        try:
            kickoff = _parse_espn_datetime(raw_date)
        except ValueError:
            continue

        scorers: List[str] = []
        for det in comp.get("details") or []:
            if det.get("scoringPlay"):
                athletes = det.get("athletesInvolved") or []
                scorer_name = athletes[0].get("displayName") if athletes else None
                minute = (det.get("clock") or {}).get("displayValue", "")
                if scorer_name:
                    scorers.append(f"{scorer_name} ({minute})" if minute else scorer_name)

        matches.append(
            MatchInfo(
                competition=competition,
                home_team=home_name,
                away_team=away_name,
                home_score=_safe_int(home.get("score")) if state != "pre" else None,
                away_score=_safe_int(away.get("score")) if state != "pre" else None,
                status_state=state,
                status_detail=detail,
                kickoff_utc=kickoff,
                goal_scorers=scorers,
                event_id=str(event.get("id") or ""),
                status_type_name=type_name,
            )
        )
    return matches


def detect_match_state(
    barca_matches: List[MatchInfo],
    rival_matches: List[MatchInfo],
    now: datetime,
) -> tuple[MatchState, Optional[MatchInfo]]:
    """Determine the current emotional mood state (including exact Half-Time & Full-Time)."""
    # 1. Live Barca match takes top priority (with dedicated Half-Time states!)
    for m in barca_matches:
        if m.status_state == "in":
            outcome = m.barca_outcome()
            if m.is_halftime:
                if outcome == "win":
                    return MatchState.BARCA_HALFTIME_WINNING, m
                return MatchState.BARCA_HALFTIME_STRUGGLING, m
            if outcome == "win":
                return MatchState.BARCA_LIVE_WINNING, m
            return MatchState.BARCA_LIVE_STRUGGLING, m

    # 2. Recently finished Barca match (within 12 hours after kickoff)
    recent_post = [
        m
        for m in barca_matches
        if m.status_state == "post" and timedelta(0) <= (now - m.kickoff_utc) <= timedelta(hours=12)
    ]
    if recent_post:
        latest = max(recent_post, key=lambda x: x.kickoff_utc)
        if latest.barca_outcome() == "win":
            return MatchState.BARCA_POST_WIN, latest
        return MatchState.BARCA_POST_DROP, latest

    # 3. Upcoming Barca match within next 10 hours -> pre-match delusional hype
    upcoming_pre = [
        m
        for m in barca_matches
        if m.status_state == "pre" and timedelta(minutes=-15) <= (m.kickoff_utc - now) <= timedelta(hours=10)
    ]
    if upcoming_pre:
        soonest = min(upcoming_pre, key=lambda x: x.kickoff_utc)
        return MatchState.BARCA_PRE_MATCH, soonest

    # 4. Rival drama (Real Madrid / Atleti / UCL rival playing now, soon, or just dropped points)
    for m in rival_matches:
        if m.status_state == "in":
            return MatchState.RIVAL_MATCH_DRAMA, m
    rival_drops = [
        m
        for m in rival_matches
        if m.status_state == "post"
        and m.rival_dropped_points()
        and timedelta(0) <= (now - m.kickoff_utc) <= timedelta(hours=14)
    ]
    if rival_drops:
        return MatchState.RIVAL_MATCH_DRAMA, max(rival_drops, key=lambda x: x.kickoff_utc)
    rival_soon = [
        m
        for m in rival_matches
        if m.status_state == "pre" and timedelta(0) <= (m.kickoff_utc - now) <= timedelta(hours=6)
    ]
    if rival_soon:
        return MatchState.RIVAL_MATCH_DRAMA, min(rival_soon, key=lambda x: x.kickoff_utc)

    return MatchState.OFF_DAY_BANTER, None


def _fetch_espn_matches(now: datetime) -> tuple[List[MatchInfo], List[MatchInfo]]:
    date_keys = [
        (now - timedelta(days=1)).strftime("%Y%m%d"),
        now.strftime("%Y%m%d"),
        (now + timedelta(days=1)).strftime("%Y%m%d"),
    ]

    barca_seen: Dict[tuple, MatchInfo] = {}
    rival_seen: Dict[tuple, MatchInfo] = {}
    headers = {"User-Agent": USER_AGENT}

    for slug, comp_name in ESPN_LEAGUES:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
        for day_str in date_keys:
            try:
                resp = requests.get(url, params={"dates": day_str}, headers=headers, timeout=HTTP_TIMEOUT)
                resp.raise_for_status()
                events = parse_espn_events(resp.json(), comp_name)
                for match in events:
                    dedup_key = (match.competition, match.home_team, match.away_team, match.kickoff_utc.isoformat())
                    if match.involves_barca:
                        barca_seen[dedup_key] = match
                    elif match.involves_rival:
                        rival_seen[dedup_key] = match
            except Exception as exc:
                logger.debug("ESPN scoreboard fetch failed for %s (%s): %s", slug, day_str, exc)

    barca_matches = sorted(barca_seen.values(), key=lambda m: m.kickoff_utc)
    rival_matches = sorted(rival_seen.values(), key=lambda m: m.kickoff_utc)
    return barca_matches, rival_matches


def _fetch_rss_headlines() -> List[str]:
    headlines: List[str] = []
    headers = {"User-Agent": USER_AGENT}
    keywords = (
        "barca",
        "barcelona",
        "yamal",
        "pedri",
        "flick",
        "raphinha",
        "lewandowski",
        "gavi",
        "real madrid",
        "vinicius",
        "mbappe",
        "bellingham",
        "atletico",
        "la liga",
        "champions league",
        "arsenal",
        "man city",
        "haaland",
    )

    for source_name, url in RSS_FEEDS:
        try:
            resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            titles: List[str] = []
            for item in root.findall(".//item/title"):
                if item.text:
                    titles.append(html.unescape(item.text.strip()))
            if not titles:
                for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title"):
                    if entry.text:
                        titles.append(html.unescape(entry.text.strip()))

            added_for_source = 0
            for title in titles:
                low = title.lower()
                if source_name in {"Barca Blaugranes", "Managing Madrid"} or any(k in low for k in keywords):
                    headlines.append(f"[{source_name}] {title}")
                    added_for_source += 1
                    if added_for_source >= 4:
                        break
        except Exception as exc:
            logger.debug("RSS fetch failed for %s: %s", source_name, exc)

    return headlines


def fetch_post_match_google_news(opponent: Optional[str]) -> List[str]:
    """Pull freshest breaking post-match FC Barcelona headlines via free Google News RSS."""
    query = f"FC Barcelona {opponent} match when:1d" if opponent else "FC Barcelona match result when:1d"
    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    items: List[str] = []
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item/title")[:6]:
            if item.text:
                items.append(f"[Breaking] {html.unescape(item.text.strip())}")
    except Exception as exc:
        logger.debug("Google News RSS fetch failed: %s", exc)
    return items


def fetch_live_context(now: Optional[datetime] = None) -> LiveFootballContext:
    """Gather current Barca & rival match state + fresh football news headlines."""
    current = now or datetime.now(timezone.utc)
    barca_matches, rival_matches = _fetch_espn_matches(current)
    headlines = _fetch_rss_headlines()
    match_state, focus_match = detect_match_state(barca_matches, rival_matches, current)

    post_match_news: List[str] = []
    if match_state in {
        MatchState.BARCA_HALFTIME_WINNING,
        MatchState.BARCA_HALFTIME_STRUGGLING,
        MatchState.BARCA_POST_WIN,
        MatchState.BARCA_POST_DROP,
    }:
        opponent = focus_match.opponent_of_barca() if focus_match else None
        post_match_news = fetch_post_match_google_news(opponent)

    epl_slander_alerts: List[str] = []
    for m in barca_matches + rival_matches:
        if m.status_state in {"in", "post"} and timedelta(hours=-1) <= (current - m.kickoff_utc) <= timedelta(hours=36):
            if m.laliga_beat_epl():
                epl_slander_alerts.append(
                    f"LA LIGA OWNAGE OVER INTENSITY LEAGUE: {m.summary_line()} — cook the EPL mercilessly!"
                )
            elif m.epl_dropped_in_europe():
                epl_slander_alerts.append(
                    f"INTENSITY LEAGUE FLOP IN EUROPE: {m.summary_line()} — mock EPL hype!"
                )

    return LiveFootballContext(
        fetched_at_utc=current,
        match_state=match_state,
        focus_match=focus_match,
        barca_matches=barca_matches,
        rival_matches=rival_matches,
        headlines=headlines,
        post_match_news=post_match_news,
        epl_slander_alerts=epl_slander_alerts,
    )
