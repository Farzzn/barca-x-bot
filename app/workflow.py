"""Deterministic Football Twitter Workflow Engine.

Implements the exact posting calendar:
  1. FC Barcelona Matchday (4 posts if won, 6 posts if drew/lost):
       - barca_prematch_8h  : ~8 hours before kickoff (bragging / trash talk)
       - barca_lineup       : ~1 hour before kickoff once XI announced (lineup brag)
       - barca_halftime     : at HT (bragging if winning, meltdown if drawing/losing)
       - barca_fulltime_1   : right after FT (1 brag if win; meltdown #1 if draw/loss)
       - barca_fulltime_2   : +20m after FT (ONLY if draw/loss — meltdown #2)
       - barca_fulltime_3   : +40m after FT (ONLY if draw/loss — meltdown #3)

  2. Real Madrid Matchday (3 posts total, non-Clasico):
       - madrid_lineup      : ~1 hour before kickoff once XI announced (mock XI)
       - madrid_fulltime_1  : right after FT (discredit win #1 OR deserved loss #1)
       - madrid_fulltime_2  : +25m after FT (discredit win #2 OR deserved loss #2)

  3. Thursday UCL EPL Roast (1 post on Thursdays if an EPL club lost Tue/Wed UCL):
       - thursday_ucl_epl   : mocks EPL ('Intensity League') clubs that lost in UCL

  4. Non-Matchdays (1–3 posts/day, rolling weekly target ~26 ± 4 -> avg <= 3.71/day):
       - offday_rival_banter: rotates 1–2 Big-8 clubs/day (Atletico, PSG, Liverpool,
         Man City, Bayern, Arsenal, Man Utd, Chelsea — excludes Real Madrid & small clubs)
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from app.config import RuntimeBehaviorConfig
from app.context_fetcher import (
    BIG_EIGHT_RIVALS,
    EPL_BIG_CLUBS,
    LiveFootballContext,
    MatchInfo,
)

# IST = UTC + 05:30 (matches user's timezone for Thursday & daily pacing)
IST = timezone(timedelta(hours=5, minutes=30))

# Default spacing for multi-post full-time bursts (minutes)
BARCA_LOSS_BURST_GAP_MIN = 18
MADRID_FT_BURST_GAP_MIN = 20

# Prime off-day windows in UTC (~11:30 AM IST, ~4:30 PM IST, ~9:30 PM IST)
OFFDAY_SLOT_HOURS_UTC = [6, 11, 16]


@dataclass
class WorkflowDecision:
    should_post: bool
    reason: str
    workflow_slot: str = "skip"
    trigger_type: str = "skip"
    target_club: str = "FC Barcelona"
    archetype_name: str = "rival_comparison_bait"
    slot_instructions: str = ""
    event_lock: Optional[Tuple[str, str]] = None  # (match_key, event_type)
    focus_match: Optional[MatchInfo] = None
    day_mode: str = "off_day"
    today_rotation_clubs: Tuple[str, ...] = ()


def is_epl_club(name: str) -> bool:
    low = name.lower()
    return any(c in low for c in EPL_BIG_CLUBS)


def pick_daily_big_eight_clubs(
    now_utc: datetime,
    recent_target_clubs: List[str],
    count: int = 2,
) -> List[str]:
    """Pick 1–2 Big-8 clubs for today deterministically, avoiding clubs mocked in last 3 days."""
    day_ist = now_utc.astimezone(IST).strftime("%Y-%m-%d")
    recent_set = {c.lower() for c in recent_target_clubs[:6] if c}

    fresh = [c for c in BIG_EIGHT_RIVALS if c.lower() not in recent_set]
    pool = fresh if len(fresh) >= count else list(BIG_EIGHT_RIVALS)

    # Deterministic per-day shuffle so repeated 10-min ticks on the same day agree on today's 1-2 clubs
    seed_int = int(hashlib.sha256(f"barca-big8-{day_ist}".encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed_int)
    shuffled = list(pool)
    rng.shuffle(shuffled)
    return shuffled[: max(1, min(count, len(shuffled)))]


def _active_match_in_window(matches: List[MatchInfo], now: datetime) -> Optional[MatchInfo]:
    """Return the active match if one is within 8.5h before KO, live, or finished within 5.5h of KO."""
    # 1. Live match first
    for m in matches:
        if m.status_state == "in":
            return m
    # 2. Recently finished match (within 6h of kickoff -> covers ~4h post-whistle for 3 FT bursts)
    recent_post = [
        m for m in matches if m.status_state == "post" and timedelta(0) <= (now - m.kickoff_utc) <= timedelta(hours=6)
    ]
    if recent_post:
        return max(recent_post, key=lambda x: x.kickoff_utc)
    # 3. Upcoming match within 8.5 hours of kickoff
    upcoming = [
        m
        for m in matches
        if m.status_state == "pre" and timedelta(minutes=-15) <= (m.kickoff_utc - now) <= timedelta(hours=8, minutes=30)
    ]
    if upcoming:
        return min(upcoming, key=lambda x: x.kickoff_utc)
    return None


def _evaluate_barca_matchday(
    match: MatchInfo,
    behavior: RuntimeBehaviorConfig,
    store,
    now: datetime,
) -> Optional[WorkflowDecision]:
    """Run exact Barça Matchday sequence: 8h pre-match -> Lineup -> HT -> FT (1 win / 3 draw-loss)."""
    mkey = match.match_key
    opp = match.opponent_of_barca() or "our opponent"
    time_to_ko = match.kickoff_utc - now
    barca_lu = match.lineup_for_barca()
    outcome = match.barca_outcome()
    scorers_str = ", ".join(match.goal_scorers) if match.goal_scorers else "none listed yet"

    # SLOT 1: Pre-match brag (~8 hours before kickoff, while still > 1.5h before KO)
    if (
        match.status_state == "pre"
        and timedelta(hours=1, minutes=30) < time_to_ko <= timedelta(hours=8, minutes=30)
        and behavior.auto_post_prematch
        and not store.has_sent_match_event(mkey, "barca_prematch_8h")
    ):
        return WorkflowDecision(
            should_post=True,
            reason=f"Barca Matchday Slot 1 (8h Pre-Match): {match.summary_line()}",
            workflow_slot="barca_prematch_8h",
            trigger_type="matchday",
            target_club="FC Barcelona",
            archetype_name="pre_match_delusion",
            slot_instructions=(
                f"BARCA MATCHDAY — PRE-MATCH BRAG (~8 HOURS BEFORE KICKOFF vs {opp}). "
                f"Brag shamelessly about FC Barcelona's squad, Flick's football, and our stars (Pedri, Lamine Yamal, Raphinha, Anthony Gordon, Fermin, Cubarsi). "
                f"Talk trash about {opp} standing zero chance tonight. End with deadpan confidence (e.g. 'I pity {opp}.' or bold scoreline + 'End.')."
            ),
            event_lock=(mkey, "barca_prematch_8h"),
            focus_match=match,
            day_mode="barca_matchday",
        )

    # SLOT 2: Lineup Announced (~1h before kickoff once XI is published, or fallback <=35m before KO)
    lineup_ready = bool(barca_lu and barca_lu.is_announced)
    if (
        match.status_state == "pre"
        and timedelta(minutes=-5) <= time_to_ko <= timedelta(minutes=95)
        and (lineup_ready or time_to_ko <= timedelta(minutes=35))
        and behavior.auto_post_prematch
        and not store.has_sent_match_event(mkey, "barca_lineup")
    ):
        xi_text = barca_lu.format_line() if lineup_ready and barca_lu else "Starting XI just dropped"
        return WorkflowDecision(
            should_post=True,
            reason=f"Barca Matchday Slot 2 (Lineup Announced): {xi_text}",
            workflow_slot="barca_lineup",
            trigger_type="lineup",
            target_club="FC Barcelona",
            archetype_name="pre_match_delusion",
            slot_instructions=(
                f"BARCA MATCHDAY — OFFICIAL LINEUP JUST DROPPED vs {opp}! {xi_text}. "
                f"React directly to specific starters in that XI — brag that {opp} won't survive 30 minutes against this lineup, "
                f"predict a ruthless victory, pure overconfident Football Twitter energy!"
            ),
            event_lock=(mkey, "barca_lineup"),
            focus_match=match,
            day_mode="barca_matchday",
        )

    # SLOT 3: Half-Time Reaction (winning -> brag; drawing/losing -> meltdown!)
    if (
        match.is_halftime
        and behavior.auto_post_halftime
        and not store.has_sent_match_event(mkey, "barca_halftime")
    ):
        if outcome == "win":
            return WorkflowDecision(
                should_post=True,
                reason=f"Barca Matchday Slot 3 (Half-Time WINNING): {match.summary_line()}",
                workflow_slot="barca_halftime",
                trigger_type="halftime",
                target_club="FC Barcelona",
                archetype_name="post_win_hype",
                slot_instructions=(
                    f"HALF-TIME IN BARCA'S MATCH — WE ARE WINNING! ({match.summary_line()} | Scorers: {scorers_str}). "
                    f"Drop an unhinged bragging half-time post flexing the scoreline, praising our goalscorers/Flick, "
                    f"and declaring the trophy/treble already ours."
                ),
                event_lock=(mkey, "barca_halftime"),
                focus_match=match,
                day_mode="barca_matchday",
            )
        return WorkflowDecision(
            should_post=True,
            reason=f"Barca Matchday Slot 3 (Half-Time STRUGGLING): {match.summary_line()}",
            workflow_slot="barca_halftime",
            trigger_type="halftime",
            target_club="FC Barcelona",
            archetype_name="post_drop_meltdown",
            slot_instructions=(
                f"HALF-TIME IN BARCA'S MATCH — WE ARE NOT WINNING! ({match.summary_line()} | Scorers: {scorers_str}). "
                f"Flip into instant dramatic half-time Football Twitter meltdown! Comedic overreaction like "
                f"'Send Anthony Gordon back to Newcastle', 'Flick out', 'tactically empty', or calling out whoever is strolling on the pitch. "
                f"Keep it strictly football banter, raw and hilarious."
            ),
            event_lock=(mkey, "barca_halftime"),
            focus_match=match,
            day_mode="barca_matchday",
        )

    # SLOT 4 (and 5 & 6 if draw/loss): Full-Time Reaction
    if match.is_fulltime and behavior.auto_post_fulltime:
        if outcome == "win":
            if not store.has_sent_match_event(mkey, "barca_fulltime_1"):
                return WorkflowDecision(
                    should_post=True,
                    reason=f"Barca Matchday Slot 4 (Full-Time WIN — 1 brag post): {match.summary_line()}",
                    workflow_slot="barca_fulltime_1",
                    trigger_type="fulltime",
                    target_club="FC Barcelona",
                    archetype_name="post_win_hype",
                    slot_instructions=(
                        f"FULL-TIME — BARCA WON! ({match.summary_line()} | Scorers: {scorers_str}). "
                        f"Write ONE triumphant bragging post reacting to the final score, scorers, and breaking post-match news. "
                        f"Hype the squad, warn Real Madrid and the Intensity League, pure euphoria!"
                    ),
                    event_lock=(mkey, "barca_fulltime_1"),
                    focus_match=match,
                    day_mode="barca_matchday",
                )
        else:
            # Draw or Loss -> 3 distinct frustration posts spaced ~18-20 min apart!
            ft_angles = [
                (
                    "barca_fulltime_1",
                    f"FULL-TIME MELTDOWN POST 1 OF 3 — BARCA DROPPED POINTS ({match.summary_line()} | Scorers: {scorers_str}). "
                    f"Instant raw shock & manager meltdown: 'FLICK OUT', 'Individual brilliance was the only thing saving him, tactically empty', "
                    f"reacting straight to the final score and breaking post-match news!",
                ),
                (
                    "barca_fulltime_2",
                    f"FULL-TIME MELTDOWN POST 2 OF 3 — BARCA DROPPED POINTS ({match.summary_line()}). "
                    f"Frustrated player callout angle (DIFFERENT from Post 1): dramatically complain about players walking on the pitch, "
                    f"overpaid starters not caring about the badge ('We lied to ourselves', 'Send Gordon back to Newcastle' / sell underperformer from today's XI).",
                ),
                (
                    "barca_fulltime_3",
                    f"FULL-TIME MELTDOWN POST 3 OF 3 — BARCA DROPPED POINTS ({match.summary_line()}). "
                    f"Cold dramatic reality-check / pattern-of-shame angle (DIFFERENT from Posts 1 & 2): "
                    f"'It is not just about dropping points, it is about WHO we are dropping points to' or demanding a full squad mentality wake-up call.",
                ),
            ]
            last_ft_time = store.last_match_event_time(mkey, ["barca_fulltime_1", "barca_fulltime_2"])
            for slot_name, instructions in ft_angles:
                if store.has_sent_match_event(mkey, slot_name):
                    continue
                if slot_name != "barca_fulltime_1" and last_ft_time is not None:
                    elapsed = (now - last_ft_time).total_seconds() / 60.0
                    if elapsed < BARCA_LOSS_BURST_GAP_MIN:
                        return WorkflowDecision(
                            should_post=False,
                            reason=f"Waiting for next Barca FT meltdown burst ({slot_name}: {elapsed:.0f}m/{BARCA_LOSS_BURST_GAP_MIN}m elapsed).",
                            day_mode="barca_matchday",
                            focus_match=match,
                        )
                return WorkflowDecision(
                    should_post=True,
                    reason=f"Barca Matchday Full-Time Frustration ({slot_name}): {match.summary_line()}",
                    workflow_slot=slot_name,
                    trigger_type="fulltime",
                    target_club="FC Barcelona",
                    archetype_name="post_drop_meltdown",
                    slot_instructions=instructions,
                    event_lock=(mkey, slot_name),
                    focus_match=match,
                    day_mode="barca_matchday",
                )

    return WorkflowDecision(
        should_post=False,
        reason=f"Barca matchday active ({match.summary_line()}); waiting for next match milestone (8h/Lineup/HT/FT).",
        day_mode="barca_matchday",
        focus_match=match,
    )


def _evaluate_madrid_matchday(
    match: MatchInfo,
    behavior: RuntimeBehaviorConfig,
    store,
    now: datetime,
) -> Optional[WorkflowDecision]:
    """Run exact Real Madrid Matchday sequence: Lineup (1) -> Full-Time (2 posts)."""
    if not behavior.auto_post_rival_drama:
        return None
    mkey = match.match_key
    opp = match.opponent_of_madrid() or "their opponent"
    time_to_ko = match.kickoff_utc - now
    madrid_lu = match.lineup_for_madrid()
    outcome = match.madrid_outcome()
    scorers_str = ", ".join(match.goal_scorers) if match.goal_scorers else "none"

    # SLOT 1: Real Madrid Lineup Announced (~1h before KO, fallback <=35m before KO)
    lineup_ready = bool(madrid_lu and madrid_lu.is_announced)
    if (
        match.status_state == "pre"
        and timedelta(minutes=-5) <= time_to_ko <= timedelta(minutes=95)
        and (lineup_ready or time_to_ko <= timedelta(minutes=35))
        and not store.has_sent_match_event(mkey, "madrid_lineup")
    ):
        xi_text = madrid_lu.format_line() if lineup_ready and madrid_lu else "Real Madrid XI just dropped"
        return WorkflowDecision(
            should_post=True,
            reason=f"Real Madrid Matchday Slot 1 (Lineup Trash Talk): {xi_text}",
            workflow_slot="madrid_lineup",
            trigger_type="lineup",
            target_club="Real Madrid",
            archetype_name="rival_match_slander",
            slot_instructions=(
                f"REAL MADRID MATCHDAY — LINEUP JUST ANNOUNCED vs {opp}! {xi_text}. "
                f"Mock Real Madrid's Starting XI relentlessly! Roast their unbalanced midfield, overrated PR stars, or 'haram ball' tactics, "
                f"and boldly predict {opp} will embarrass them today (e.g., 'Real Madrid 1-2 {opp}. End.')."
            ),
            event_lock=(mkey, "madrid_lineup"),
            focus_match=match,
            day_mode="madrid_matchday",
        )

    # SLOTS 2 & 3: Real Madrid Full-Time (2 posts whether they won OR lost/drew!)
    if match.is_fulltime:
        if outcome == "win":
            ft_angles = [
                (
                    "madrid_fulltime_1",
                    f"REAL MADRID FULL-TIME — THEY WON ({match.summary_line()} | Scorers: {scorers_str}). "
                    f"POST 1 OF 2 DISCREDITING THEIR VICTORY (bitter, frustrated Culer energy): "
                    f"Blame refereeing favors, VAR rigging, pure luck, or individual bailouts — refuse to give Real Madrid any tactical credit!",
                ),
                (
                    "madrid_fulltime_2",
                    f"REAL MADRID FULL-TIME — THEY WON ({match.summary_line()}). "
                    f"POST 2 OF 2 DISCREDITING THEIR VICTORY (DIFFERENT angle from Post 1): "
                    f"Mock {opp} as farmers/bottlers and call out Real Madrid's PR machine for hyping a shameless win — insist they will get exposed against a real team.",
                ),
            ]
        else:
            ft_angles = [
                (
                    "madrid_fulltime_1",
                    f"REAL MADRID FULL-TIME — THEY DROPPED POINTS / LOST ({match.summary_line()} | Scorers: {scorers_str})! "
                    f"POST 1 OF 2 CONFIRMING WHY THEY DESERVED TO LOSE: "
                    f"Cook their tactics, zero midfield control, and overrated stars getting exposed by {opp}. Pure Football Twitter gloating!",
                ),
                (
                    "madrid_fulltime_2",
                    f"REAL MADRID FULL-TIME — THEY DROPPED POINTS / LOST ({match.summary_line()})! "
                    f"POST 2 OF 2 CONFIRMING WHY THEY DESERVED TO LOSE (DIFFERENT angle from Post 1): "
                    f"Mock Real Madrid fans, their Ballon d'Or / Galacticos PR hype, and how even the referees could not bail them out today.",
                ),
            ]

        last_ft_time = store.last_match_event_time(mkey, ["madrid_fulltime_1"])
        for slot_name, instructions in ft_angles:
            if store.has_sent_match_event(mkey, slot_name):
                continue
            if slot_name == "madrid_fulltime_2" and last_ft_time is not None:
                elapsed = (now - last_ft_time).total_seconds() / 60.0
                if elapsed < MADRID_FT_BURST_GAP_MIN:
                    return WorkflowDecision(
                        should_post=False,
                        reason=f"Waiting for Madrid FT Post 2 ({elapsed:.0f}m/{MADRID_FT_BURST_GAP_MIN}m elapsed).",
                        day_mode="madrid_matchday",
                        focus_match=match,
                    )
            return WorkflowDecision(
                should_post=True,
                reason=f"Real Madrid Matchday Full-Time ({slot_name}, outcome={outcome}): {match.summary_line()}",
                workflow_slot=slot_name,
                trigger_type="fulltime",
                target_club="Real Madrid",
                archetype_name="rival_match_slander",
                slot_instructions=instructions,
                event_lock=(mkey, slot_name),
                focus_match=match,
                day_mode="madrid_matchday",
            )

    return WorkflowDecision(
        should_post=False,
        reason=f"Real Madrid matchday active ({match.summary_line()}); waiting for Lineup or Full-Time.",
        day_mode="madrid_matchday",
        focus_match=match,
    )


def _evaluate_thursday_ucl_epl(
    context: LiveFootballContext,
    store,
    now: datetime,
) -> Optional[WorkflowDecision]:
    """Every Thursday: if UCL happened Tue/Wed & any EPL club lost, fire 1 Intensity League roast."""
    now_ist = now.astimezone(IST)
    # weekday(): Mon=0, Tue=1, Wed=2, Thu=3
    if now_ist.weekday() != 3:
        return None
    # Fire after 11:00 AM IST (05:30 UTC) on Thursday
    if now_ist.hour < 11:
        return None
    if not context.ucl_recent_epl_losses:
        return None

    thursday_key = f"thursday_ucl:{now_ist.strftime('%Y-%m-%d')}"
    if store.has_sent_match_event(thursday_key, "thursday_ucl_epl"):
        return None

    losing_clubs = [m.epl_lost_in_ucl() for m in context.ucl_recent_epl_losses if m.epl_lost_in_ucl()]
    unique_losers = list(dict.fromkeys(losing_clubs))
    target_club = unique_losers[0] if len(unique_losers) == 1 else "EPL (Intensity League)"
    summaries = "; ".join(m.summary_line() for m in context.ucl_recent_epl_losses[:4])

    return WorkflowDecision(
        should_post=True,
        reason=f"Thursday UCL EPL Flop Recap fired for: {', '.join(unique_losers)}",
        workflow_slot="thursday_ucl_epl",
        trigger_type="thursday_ucl",
        target_club=target_club,
        archetype_name="epl_intensity_slander",
        slot_instructions=(
            f"THURSDAY UCL RECAP — EPL ('INTENSITY LEAGUE') TEAMS LOST IN CHAMPIONS LEAGUE THIS WEEK: {summaries}. "
            f"Roast {', '.join(unique_losers)} and the whole English Premier League mercilessly! "
            f"Push La Liga > EPL supremacy, call the EPL the 'Intensity League' and use sharp banter like "
            f"'Intensity League teams are trash', 'billion-pound cardio merchants exposed in Europe again'."
        ),
        event_lock=(thursday_key, "thursday_ucl_epl"),
        focus_match=context.ucl_recent_epl_losses[0],
        day_mode="thursday_ucl_epl",
    )


def evaluate_workflow(
    behavior: RuntimeBehaviorConfig,
    store,
    context: LiveFootballContext,
    now: Optional[datetime] = None,
) -> WorkflowDecision:
    """Master decision engine called on every 10-minute Heroku Scheduler tick."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if not behavior.bot_enabled:
        return WorkflowDecision(
            should_post=False,
            reason="Bot is paused via Admin Dashboard (`bot_enabled=false`).",
            workflow_slot="paused",
            trigger_type="paused",
        )

    # 1. Check Thursday UCL EPL Flop Roast (independent bonus slot when EPL lost in Tue/Wed UCL!)
    thu_decision = _evaluate_thursday_ucl_epl(context, store, current)
    if thu_decision is not None:
        return thu_decision

    # 2. Check FC Barcelona Matchday (priority #1: 4 posts if won, 6 posts if drew/lost)
    active_barca = _active_match_in_window(context.barca_matches, current)
    if active_barca is not None:
        barca_decision = _evaluate_barca_matchday(active_barca, behavior, store, current)
        if barca_decision is not None and barca_decision.should_post:
            return barca_decision

    # 3. Check Real Madrid Matchday (3 posts: lineup + 2 FT posts)
    active_madrid = _active_match_in_window(context.madrid_matches, current)
    if active_madrid is not None:
        madrid_decision = _evaluate_madrid_matchday(active_madrid, behavior, store, current)
        if madrid_decision is not None and madrid_decision.should_post:
            return madrid_decision

    # If either Barça or Real Madrid has an active matchday right now, do NOT mix in generic off-day posts!
    if active_barca is not None:
        return WorkflowDecision(
            should_post=False,
            reason=f"Barca matchday active ({active_barca.summary_line()}); waiting for next match trigger (no off-day spam).",
            day_mode="barca_matchday",
            focus_match=active_barca,
        )
    if active_madrid is not None:
        return WorkflowDecision(
            should_post=False,
            reason=f"Real Madrid matchday active ({active_madrid.summary_line()}); waiting for Lineup/FT trigger.",
            day_mode="madrid_matchday",
            focus_match=active_madrid,
        )

    # 4. NON-MATCHDAY: 1–3 posts/day rotating Big-8 clubs while staying within weekly budget (~26 ± 4, avg <= 3.71/day)
    posts_today = store.count_posts_today_ist(current)
    posts_last_7d = store.count_posts_rolling_days(days=7, now=current)
    weekly_target = behavior.weekly_post_target  # default 26
    min_daily = behavior.non_matchday_min_posts  # default 1
    max_daily = behavior.non_matchday_max_posts  # default 3

    # Dynamic daily quota keeps rolling 7-day average <= 3.71/day (22–30/week)
    if posts_last_7d >= weekly_target - 1:
        today_quota = min_daily  # Busy match week -> throttle off-day to 1 post/day
    elif posts_last_7d >= weekly_target - 4:
        today_quota = min(max_daily, max(min_daily, 2))
    else:
        today_quota = max_daily  # Quiet week -> 3 posts/day to stay near 26/week

    recent_targets = store.get_recent_target_clubs(limit=12)
    today_clubs = pick_daily_big_eight_clubs(current, recent_targets, count=min(2, today_quota))

    if posts_today >= today_quota:
        return WorkflowDecision(
            should_post=False,
            reason=(
                f"Off-day quota complete ({posts_today}/{today_quota} today IST; "
                f"7-day total={posts_last_7d}/{weekly_target}, avg={posts_last_7d / 7.0:.2f}/day)."
            ),
            day_mode="off_day",
            today_rotation_clubs=tuple(today_clubs),
        )

    # Enforce active off-day slot hours (UTC) & minimum spacing between off-day posts
    slot_hours = behavior.post_hours_utc or OFFDAY_SLOT_HOURS_UTC
    if current.hour not in slot_hours:
        return WorkflowDecision(
            should_post=False,
            reason=(
                f"Off-day waiting for next UTC slot hour (now={current.hour:02d} UTC, "
                f"slots={slot_hours}; quota {posts_today}/{today_quota}, rotation={today_clubs})."
            ),
            day_mode="off_day",
            today_rotation_clubs=tuple(today_clubs),
        )

    last_post = store.last_live_post_time()
    if last_post is not None:
        elapsed_min = (current - last_post).total_seconds() / 60.0
        if elapsed_min < behavior.min_minutes_between_posts:
            return WorkflowDecision(
                should_post=False,
                reason=f"Off-day cooldown ({elapsed_min:.0f}m < {behavior.min_minutes_between_posts}m).",
                day_mode="off_day",
                today_rotation_clubs=tuple(today_clubs),
            )

    # Rotate cleanly across today's 1–2 chosen Big-8 clubs
    chosen_club = today_clubs[posts_today % len(today_clubs)]
    if is_epl_club(chosen_club):
        archetype = random.choice(["epl_intensity_slander", "epl_intensity_slander", "rival_comparison_bait"])
        slot_instructions = (
            f"NON-MATCHDAY BIG-CLUB BANTER — TARGET CLUB: {chosen_club} (English Premier League / 'Intensity League'). "
            f"Trash talk {chosen_club}, their overrated players, their manager, or their delusional fanbase! "
            f"Push the La Liga > EPL ('Intensity League') narrative, compare their stars unfavorably to Barça players "
            f"(Pedri, Lamine Yamal, Raphinha, Cubarsi, Fermin, Anthony Gordon), and provoke {chosen_club} fans to reply!"
        )
    else:
        archetype = random.choice(["rival_comparison_bait", "rival_match_slander", "unpopular_one_percent"])
        slot_instructions = (
            f"NON-MATCHDAY BIG-CLUB BANTER — TARGET CLUB: {chosen_club}. "
            f"Trash talk {chosen_club}, their players, their manager, or their fanbase! "
            f"Start a player comparison war with Barça stars, roast their European choke jobs or playing style, "
            f"and make {chosen_club} fans jump into the replies!"
        )

    return WorkflowDecision(
        should_post=True,
        reason=(
            f"Off-day slot {posts_today + 1}/{today_quota} targeting {chosen_club} "
            f"(7d total={posts_last_7d}/{weekly_target}, avg={posts_last_7d / 7.0:.2f}/day)."
        ),
        workflow_slot="offday_rival_banter",
        trigger_type="scheduled",
        target_club=chosen_club,
        archetype_name=archetype,
        slot_instructions=slot_instructions,
        day_mode="off_day",
        today_rotation_clubs=tuple(today_clubs),
    )
