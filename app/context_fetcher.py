"""Live FC Barcelona, Real Madrid, UCL & Big-8 Rival match/lineup/news context fetcher.

Free public endpoints used (zero API cost):
  - ESPN Soccer Scoreboards: La Liga, UCL, Europa League, Copa del Rey, Supercopa, EPL
  - ESPN Soccer Match Summary (`/summary?event=<id>`): Official Starting XI & formation (~1h before KO)
  - Football RSS feeds + Google News breaking post-match RSS
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
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 10
USER_AGENT = "BarcaXBot/2.0 (+https://github.com/Farzzn/barca-x-bot)"

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
REAL_MADRID_ALIASES = {"real madrid"}

# Exactly the Big-8 off-day banter targets requested (excludes Real Madrid & small clubs)
BIG_EIGHT_RIVALS: List[str] = [
    "Atletico Madrid",
    "PSG",
    "Liverpool",
    "Manchester City",
    "Bayern Munich",
    "Arsenal",
    "Manchester United",
    "Chelsea",
]

EPL_BIG_CLUBS = {
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
}


class MatchState(str, Enum):
    """High-level football mood state."""

    BARCA_PRE_MATCH = "barca_pre_match"
    BARCA_LINEUP = "barca_lineup"
    BARCA_HALFTIME_WINNING = "barca_halftime_winning"
    BARCA_HALFTIME_STRUGGLING = "barca_halftime_struggling"
    BARCA_LIVE_WINNING = "barca_live_winning"
    BARCA_LIVE_STRUGGLING = "barca_live_struggling"
    BARCA_POST_WIN = "barca_post_win"
    BARCA_POST_DROP = "barca_post_drop"
    MADRID_LINEUP = "madrid_lineup"
    MADRID_POST_WIN = "madrid_post_win"
    MADRID_POST_DROP = "madrid_post_drop"
    THURSDAY_UCL_EPL = "thursday_ucl_epl"
    RIVAL_MATCH_DRAMA = "rival_match_drama"
    OFF_DAY_BANTER = "off_day_banter"


@dataclass
class LineupInfo:
    team_name: str
    formation: str = ""
    starters: List[str] = field(default_factory=list)
    substitutes: List[str] = field(default_factory=list)

    @property
    def is_announced(self) -> bool:
        return len(self.starters) >= 11

    def format_line(self) -> str:
        form = f" ({self.formation})" if self.formation else ""
        return f"{self.team_name}{form} XI: {', '.join(self.starters[:11])}"


@dataclass
class MatchInfo:
    competition: str
    league_slug: str
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
    home_lineup: Optional[LineupInfo] = None
    away_lineup: Optional[LineupInfo] = None

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
    def involves_real_madrid(self) -> bool:
        return self._is_real_madrid(self.home_team) or self._is_real_madrid(self.away_team)

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
    def _is_real_madrid(name: str) -> bool:
        low = name.lower()
        return any(alias in low for alias in REAL_MADRID_ALIASES)

    @staticmethod
    def _is_rival(name: str) -> bool:
        low = name.lower()
        return any(alias in low for alias in RIVAL_ALIASES)

    @staticmethod
    def _is_epl(name: str) -> bool:
        low = name.lower()
        return any(club in low for club in EPL_BIG_CLUBS)

    @staticmethod
    def _is_laliga(name: str) -> bool:
        low = name.lower()
        return any(club in low for club in LALIGA_CLUBS)

    def outcome_for(self, predicate) -> Optional[str]:
        if self.home_score is None or self.away_score is None:
            return None
        if predicate(self.home_team):
            own, opp = self.home_score, self.away_score
        elif predicate(self.away_team):
            own, opp = self.away_score, self.home_score
        else:
            return None
        if own > opp:
            return "win"
        if own == opp:
            return "draw"
        return "loss"

    def barca_outcome(self) -> Optional[str]:
        return self.outcome_for(self._is_barca)

    def madrid_outcome(self) -> Optional[str]:
        return self.outcome_for(self._is_real_madrid)

    def opponent_of_barca(self) -> Optional[str]:
        if not self.involves_barca:
            return None
        return self.away_team if self._is_barca(self.home_team) else self.home_team

    def opponent_of_madrid(self) -> Optional[str]:
        if not self.involves_real_madrid:
            return None
        return self.away_team if self._is_real_madrid(self.home_team) else self.home_team

    def lineup_for_barca(self) -> Optional[LineupInfo]:
        if not self.involves_barca:
            return None
        return self.home_lineup if self._is_barca(self.home_team) else self.away_lineup

    def lineup_for_madrid(self) -> Optional[LineupInfo]:
        if not self.involves_real_madrid:
            return None
        return self.home_lineup if self._is_real_madrid(self.home_team) else self.away_lineup

    def laliga_beat_epl(self) -> bool:
        if not (self.involves_laliga and self.involves_epl):
            return False
        if self.home_score is None or self.away_score is None:
            return False
        if self._is_laliga(self.home_team) and self._is_epl(self.away_team):
            return self.home_score > self.away_score
        if self._is_laliga(self.away_team) and self._is_epl(self.home_team):
            return self.away_score > self.home_score
        return False

    def epl_lost_in_ucl(self) -> Optional[str]:
        """If an EPL club LOST this UEFA Champions League match, return that EPL club's name."""
        if self.league_slug != "uefa.champions" or self.status_state != "post":
            return None
        if self.home_score is None or self.away_score is None:
            return None
        if self._is_epl(self.home_team) and not self._is_epl(self.away_team) and self.home_score < self.away_score:
            return self.home_team
        if self._is_epl(self.away_team) and not self._is_epl(self.home_team) and self.away_score < self.home_score:
            return self.away_team
        return None

    def epl_dropped_in_europe(self) -> bool:
        if "UEFA" not in self.competition or not self.involves_epl:
            return False
        if self.home_score is None or self.away_score is None:
            return False
        if self._is_epl(self.home_team) and not self._is_epl(self.away_team):
            return self.home_score <= self.away_score
        if self._is_epl(self.away_team) and not self._is_epl(self.home_team):
            return self.away_score <= self.home_score
        return False

    def rival_dropped_points(self) -> bool:
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
    madrid_matches: List[MatchInfo] = field(default_factory=list)
    rival_matches: List[MatchInfo] = field(default_factory=list)
    ucl_recent_epl_losses: List[MatchInfo] = field(default_factory=list)
    headlines: List[str] = field(default_factory=list)
    post_match_news: List[str] = field(default_factory=list)
    epl_slander_alerts: List[str] = field(default_factory=list)

    @property
    def has_laliga_beat_epl(self) -> bool:
        return any(a.startswith("LA LIGA OWNAGE") for a in self.epl_slander_alerts)

    def to_prompt_block(self) -> str:
        lines = [
            f"Current UTC time: {self.fetched_at_utc.strftime('%Y-%m-%d %H:%M UTC')}",
            f"Detected Match State: {self.match_state.value}",
        ]
        if self.focus_match:
            lines.append(f"Primary Focus Match: {self.focus_match.summary_line()}")
            if self.focus_match.home_lineup and self.focus_match.home_lineup.is_announced:
                lines.append(f"  Home Lineup -> {self.focus_match.home_lineup.format_line()}")
            if self.focus_match.away_lineup and self.focus_match.away_lineup.is_announced:
                lines.append(f"  Away Lineup -> {self.focus_match.away_lineup.format_line()}")
        if self.barca_matches:
            lines.append("Recent & Upcoming FC Barcelona Matches:")
            for m in self.barca_matches[:4]:
                lines.append(f"  - {m.summary_line()}")
        if self.madrid_matches:
            lines.append("Recent & Upcoming Real Madrid Matches:")
            for m in self.madrid_matches[:3]:
                lines.append(f"  - {m.summary_line()}")
        if self.ucl_recent_epl_losses:
            lines.append("EPL ('Intensity League') Clubs That LOST in UCL (Tue/Wed):")
            for m in self.ucl_recent_epl_losses[:5]:
                lines.append(f"  - {m.summary_line()}")
        if self.rival_matches:
            lines.append("Other Big Rival Matches (Atleti / PSG / Bayern / EPL):")
            for m in self.rival_matches[:6]:
                lines.append(f"  - {m.summary_line()}")
        if self.epl_slander_alerts:
            lines.append("EPL 'INTENSITY LEAGUE' SLANDER ALERTS:")
            for alert in self.epl_slander_alerts[:5]:
                lines.append(f"  - {alert}")
        if self.post_match_news:
            lines.append("Breaking Live / Post-Match News:")
            for item in self.post_match_news[:6]:
                lines.append(f"  - {item}")
        if self.headlines:
            lines.append("Latest Football Headlines:")
            for h in self.headlines[:10]:
                lines.append(f"  - {h}")
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


def parse_espn_events(payload: Dict[str, Any], competition: str, league_slug: str = "esp.1") -> List[MatchInfo]:
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
                league_slug=league_slug,
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


def fetch_match_lineups(match: MatchInfo) -> Tuple[Optional[LineupInfo], Optional[LineupInfo]]:
    """Fetch official Starting XI & formation from ESPN match summary (free public API)."""
    if not match.event_id or not match.league_slug:
        return None, None
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{match.league_slug}/summary"
    try:
        resp = requests.get(
            url,
            params={"event": match.event_id},
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        rosters = resp.json().get("rosters") or []
    except Exception as exc:
        logger.debug("ESPN lineup fetch failed for event %s: %s", match.event_id, exc)
        return None, None

    home_lu: Optional[LineupInfo] = None
    away_lu: Optional[LineupInfo] = None
    for ros in rosters:
        team_name = str((ros.get("team") or {}).get("displayName") or "")
        formation = str(ros.get("formation") or "")
        entries = ros.get("roster") or []
        starters = [str((e.get("athlete") or {}).get("displayName") or "") for e in entries if e.get("starter")]
        subs = [str((e.get("athlete") or {}).get("displayName") or "") for e in entries if not e.get("starter")]
        starters = [s for s in starters if s]
        subs = [s for s in subs if s]
        lu = LineupInfo(team_name=team_name, formation=formation, starters=starters, substitutes=subs)
        ha = str(ros.get("homeAway") or "").lower()
        if ha == "home" or team_name.lower() == match.home_team.lower():
            home_lu = lu
        elif ha == "away" or team_name.lower() == match.away_team.lower():
            away_lu = lu

    match.home_lineup = home_lu
    match.away_lineup = away_lu
    return home_lu, away_lu


def detect_match_state(
    barca_matches: List[MatchInfo],
    rival_matches: List[MatchInfo],
    now: datetime,
) -> tuple[MatchState, Optional[MatchInfo]]:
    """Legacy/overview state summary helper."""
    for m in barca_matches:
        if m.status_state == "in":
            outcome = m.barca_outcome()
            if m.is_halftime:
                return (
                    MatchState.BARCA_HALFTIME_WINNING if outcome == "win" else MatchState.BARCA_HALFTIME_STRUGGLING
                ), m
            return (MatchState.BARCA_LIVE_WINNING if outcome == "win" else MatchState.BARCA_LIVE_STRUGGLING), m

    recent_post = [
        m
        for m in barca_matches
        if m.status_state == "post" and timedelta(0) <= (now - m.kickoff_utc) <= timedelta(hours=6)
    ]
    if recent_post:
        latest = max(recent_post, key=lambda x: x.kickoff_utc)
        return (MatchState.BARCA_POST_WIN if latest.barca_outcome() == "win" else MatchState.BARCA_POST_DROP), latest

    upcoming_pre = [
        m
        for m in barca_matches
        if m.status_state == "pre" and timedelta(minutes=-15) <= (m.kickoff_utc - now) <= timedelta(hours=9)
    ]
    if upcoming_pre:
        soonest = min(upcoming_pre, key=lambda x: x.kickoff_utc)
        return MatchState.BARCA_PRE_MATCH, soonest

    for m in rival_matches:
        if m.involves_real_madrid and m.status_state == "post" and timedelta(0) <= (now - m.kickoff_utc) <= timedelta(hours=6):
            return (MatchState.MADRID_POST_WIN if m.madrid_outcome() == "win" else MatchState.MADRID_POST_DROP), m

    return MatchState.OFF_DAY_BANTER, None


def _fetch_espn_matches(now: datetime) -> Tuple[List[MatchInfo], List[MatchInfo], List[MatchInfo], List[MatchInfo]]:
    """Fetch Barca, Real Madrid, big rivals, and Tue/Wed UCL matches (for Thursday EPL recap)."""
    # Yesterday, today, tomorrow for normal leagues; plus 2 days ago for UCL Thursday check!
    date_keys = [
        (now - timedelta(days=2)).strftime("%Y%m%d"),
        (now - timedelta(days=1)).strftime("%Y%m%d"),
        now.strftime("%Y%m%d"),
        (now + timedelta(days=1)).strftime("%Y%m%d"),
    ]

    barca_seen: Dict[tuple, MatchInfo] = {}
    madrid_seen: Dict[tuple, MatchInfo] = {}
    rival_seen: Dict[tuple, MatchInfo] = {}
    ucl_epl_losses: Dict[tuple, MatchInfo] = {}
    headers = {"User-Agent": USER_AGENT}

    for slug, comp_name in ESPN_LEAGUES:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
        # Only query 2-days-ago on UCL (for Thursday Tue/Wed check) to minimize HTTP calls
        days_for_league = date_keys if slug == "uefa.champions" else date_keys[1:]
        for day_str in days_for_league:
            try:
                resp = requests.get(url, params={"dates": day_str}, headers=headers, timeout=HTTP_TIMEOUT)
                resp.raise_for_status()
                events = parse_espn_events(resp.json(), comp_name, league_slug=slug)
                for match in events:
                    dedup_key = (match.competition, match.home_team, match.away_team, match.kickoff_utc.isoformat())
                    if match.involves_barca:
                        barca_seen[dedup_key] = match
                    if match.involves_real_madrid and not match.involves_barca:
                        madrid_seen[dedup_key] = match
                    if match.involves_rival and not match.involves_barca:
                        rival_seen[dedup_key] = match
                    if slug == "uefa.champions" and match.epl_lost_in_ucl():
                        ucl_epl_losses[dedup_key] = match
            except Exception as exc:
                logger.debug("ESPN scoreboard fetch failed for %s (%s): %s", slug, day_str, exc)

    barca_matches = sorted(barca_seen.values(), key=lambda m: m.kickoff_utc)
    madrid_matches = sorted(madrid_seen.values(), key=lambda m: m.kickoff_utc)
    rival_matches = sorted(rival_seen.values(), key=lambda m: m.kickoff_utc)
    epl_ucl_list = sorted(ucl_epl_losses.values(), key=lambda m: m.kickoff_utc)
    return barca_matches, madrid_matches, rival_matches, epl_ucl_list


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
        "gordon",
        "fermin",
        "cubarsi",
        "real madrid",
        "vinicius",
        "mbappe",
        "bellingham",
        "atletico",
        "psg",
        "bayern",
        "liverpool",
        "arsenal",
        "man city",
        "man utd",
        "chelsea",
        "champions league",
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


def fetch_post_match_google_news(club: str, opponent: Optional[str]) -> List[str]:
    """Pull freshest breaking post-match headlines via free Google News RSS."""
    query = f"{club} {opponent} match when:1d" if opponent else f"{club} match result when:1d"
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
    """Gather current Barca, Real Madrid, UCL EPL losses, lineups (near KO), and headlines."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    barca_matches, madrid_matches, rival_matches, ucl_epl_losses = _fetch_espn_matches(current)
    headlines = _fetch_rss_headlines()

    # Auto-fetch official ESPN lineups for any Barca or Real Madrid match within 2 hours of kickoff (or live)
    for m in barca_matches + madrid_matches:
        delta = m.kickoff_utc - current
        if m.status_state == "in" or (m.status_state == "pre" and timedelta(minutes=-15) <= delta <= timedelta(hours=2)):
            fetch_match_lineups(m)

    match_state, focus_match = detect_match_state(barca_matches, rival_matches, current)

    post_match_news: List[str] = []
    if focus_match and focus_match.status_state in {"in", "post"}:
        if focus_match.involves_barca:
            post_match_news = fetch_post_match_google_news("FC Barcelona", focus_match.opponent_of_barca())
        elif focus_match.involves_real_madrid:
            post_match_news = fetch_post_match_google_news("Real Madrid", focus_match.opponent_of_madrid())

    epl_slander_alerts: List[str] = []
    for m in barca_matches + rival_matches:
        if m.status_state in {"in", "post"} and timedelta(hours=-1) <= (current - m.kickoff_utc) <= timedelta(hours=48):
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
        madrid_matches=madrid_matches,
        rival_matches=rival_matches,
        ucl_recent_epl_losses=ucl_epl_losses,
        headlines=headlines,
        post_match_news=post_match_news,
        epl_slander_alerts=epl_slander_alerts,
    )
