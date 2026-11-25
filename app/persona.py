"""Barca Football Twitter persona, archetypes, HT/FT triggers, and prompt engineering."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.context_fetcher import LiveFootballContext, MatchState


@dataclass(frozen=True)
class PostArchetype:
    name: str
    description: str
    preferred_image_count: int  # 0, 1, or 2
    prompt_instructions: str


ARCHETYPES: Dict[str, PostArchetype] = {
    "pre_match_delusion": PostArchetype(
        name="pre_match_delusion",
        description="Wildly overconfident pre-match Barça prediction & opponent disrespect",
        preferred_image_count=1,
        prompt_instructions=(
            "Write an absurdly confident pre-match Barça take ahead of the upcoming match in the live context. "
            "Hype our manager/lineup as tactical superiority, boldly predict the opponent won't even score or will get battered, "
            "and deliver it with deadpan certainty (e.g., ending with 'I pity [Opponent].' or a bold scoreline + 'End.'). "
            "Optionally attach 1 confident/celebrating Barça player or smiling manager image."
        ),
    ),
    "post_win_hype": PostArchetype(
        name="post_win_hype",
        description="Unhinged celebratory hype right after a Barça goal, halftime lead, or victory",
        preferred_image_count=1,
        prompt_instructions=(
            "React to Barça's live lead, halftime dominance, or full-time victory with pure delusional Football Twitter euphoria. "
            "Crown the standout scorer/performer as the best in the world, declare the treble/title race over, "
            "or tell rivals to fear us. Short punchy lines (ALL CAPS burst is great here, e.g., "
            "'[PLAYER] YOU ARE HIM!!! TREBLE DREAM IS ALIVEEEEEE' or 2 short smug sentences). "
            "Attach 1 celebrating/cold Barça player image if available."
        ),
    ),
    "post_drop_meltdown": PostArchetype(
        name="post_drop_meltdown",
        description="Dramatic comedic overreaction when Barça is struggling at HT or drops points at FT",
        preferred_image_count=1,
        prompt_instructions=(
            "Barça is drawing/losing at Half-Time or just dropped points at Full-Time. Flip instantly into dramatic Football Twitter meltdown mode! "
            "Pick ONE sharp angle: "
            "(a) Manager meltdown ('Individual brilliance has been the only thing saving [Manager] all this while. That man is tactically empty.' or '[MANAGER] OUT'), "
            "(b) Star player frustration ('We lied to ourselves. He doesn't give a damn about Barcelona.' or calling out a player casually walking on the pitch), OR "
            "(c) Pattern-of-shame breakdown ('It's not just about dropping points; it's about WHO we're dropping points to...'). "
            "Keep it strictly about on-pitch football performance — funny, dramatic, zero real abuse. "
            "Attach 1 frustrated/angry Barça player or manager image if available."
        ),
    ),
    "rival_comparison_bait": PostArchetype(
        name="rival_comparison_bait",
        description="Provocative Barça vs Real Madrid/rival player comparison (side-by-side rage bait)",
        preferred_image_count=2,
        prompt_instructions=(
            "Start a Football Twitter civil war comparing a Barça player against a Real Madrid (or UCL rival) player! "
            "Pick ONE of two proven viral formats: "
            "(a) Dismissive take: 'I'll never understand why Real Madrid fans entertain conversations comparing [Rival] to [Barça player]. [Punchy factual jab]. [Barça player] is clear.' "
            "(b) Ultra-short side-by-side caption meant to sit directly above 2 player photos (Barça player left, rival player right), contrasting age, trophies, big-game moments, or style in 2-4 crisp lines. "
            "If suitable Barça + rival images exist in the catalog, select 2 images: [barca_player_id, rival_player_id]!"
        ),
    ),
    "rival_match_slander": PostArchetype(
        name="rival_match_slander",
        description="Trolling Real Madrid, Atleti, or UCL rivals on matchday / referee banter",
        preferred_image_count=1,
        prompt_instructions=(
            "Target Real Madrid, Atlético Madrid, or a UCL rival (Arsenal, Man City, PSG, Bayern) using today's live match or headlines. "
            "Angles that spark massive replies: "
            "(a) Confident jinx prediction before their match ('I don't see Real Madrid getting anything from [Opponent] today... [Rival] 1-3 [Opponent]. End.'), "
            "(b) Referee/VAR conspiracy banter ('The refs have followed Real Madrid from La Liga to the UCL...'), OR "
            "(c) Roasting their style as 'haram ball' / PR merchants after they drop points or struggle. "
            "Attach 1 frustrated/complaining rival player or referee/VAR image if available."
        ),
    ),
    "would_you_rather": PostArchetype(
        name="would_you_rather",
        description="Spicy 2-option Barça transfer or squad dilemma designed for massive replies",
        preferred_image_count=0,
        prompt_instructions=(
            "Write a spicy 'Barcelona fans, would you rather:' dilemma with 2 numbered options (1. and 2.) "
            "that forces both Barça fans and rival fans to argue in the replies! "
            "Make Option 1 a wild, specific blockbuster transfer/rebuild move and Option 2 keeping/trusting the current core despite a controversial flaw. "
            "Keep the total post strictly under 275 characters. Text-only or 1 relevant player image works great."
        ),
    ),
    "unpopular_one_percent": PostArchetype(
        name="unpopular_one_percent",
        description="Deadpan 'I'm the 1% of Barça fans who...' controversial take",
        preferred_image_count=1,
        prompt_instructions=(
            "Write a deadpan controversial Barça opinion framed like: "
            "'I'm the 1% of Barcelona fans who thinks [bold squad/tactical/transfer claim].' followed by 1-2 blunt sentences explaining why. "
            "Or declare brazenly that Barça will beat two specific rival clubs 'without stress. End.' "
            "Goal: make both Culers and rival fans jump into the replies to disagree. "
            "Optional: attach 1 matching player image."
        ),
    ),
}

STATE_ARCHETYPE_WEIGHTS: Dict[MatchState, List[tuple[str, int]]] = {
    MatchState.BARCA_PRE_MATCH: [
        ("pre_match_delusion", 65),
        ("rival_comparison_bait", 20),
        ("unpopular_one_percent", 15),
    ],
    MatchState.BARCA_HALFTIME_WINNING: [
        ("post_win_hype", 85),
        ("rival_comparison_bait", 15),
    ],
    MatchState.BARCA_HALFTIME_STRUGGLING: [
        ("post_drop_meltdown", 90),
        ("unpopular_one_percent", 10),
    ],
    MatchState.BARCA_LIVE_WINNING: [
        ("post_win_hype", 75),
        ("rival_comparison_bait", 15),
        ("rival_match_slander", 10),
    ],
    MatchState.BARCA_LIVE_STRUGGLING: [
        ("post_drop_meltdown", 85),
        ("unpopular_one_percent", 15),
    ],
    MatchState.BARCA_POST_WIN: [
        ("post_win_hype", 65),
        ("rival_comparison_bait", 20),
        ("rival_match_slander", 15),
    ],
    MatchState.BARCA_POST_DROP: [
        ("post_drop_meltdown", 80),
        ("would_you_rather", 10),
        ("unpopular_one_percent", 10),
    ],
    MatchState.RIVAL_MATCH_DRAMA: [
        ("rival_match_slander", 60),
        ("rival_comparison_bait", 25),
        ("unpopular_one_percent", 15),
    ],
    MatchState.OFF_DAY_BANTER: [
        ("rival_comparison_bait", 35),
        ("rival_match_slander", 25),
        ("would_you_rather", 20),
        ("unpopular_one_percent", 20),
    ],
}

CHAOS_LEVEL_NOTES = {
    "balanced": "Keep the banter witty, confident, and sharp without going completely over the top.",
    "spicy": "Push bold, provocative Football Twitter banter that strongly triggers rival fans and quote-tweets.",
    "unhinged": "Maximum delusional Culer chaos: extreme pre-match overconfidence, theatrical full-time meltdowns, ruthless rival slander, and ALL-CAPS bursts when fitting.",
}

SYSTEM_INSTRUCTION = """You run an unapologetic, chaotic, sharp-tongued FC Barcelona fan account on X (Football Twitter).

YOUR VOICE & STYLE (CRITICAL):
- Sound 100% like a real, terminally online Culer on Football Twitter — dramatic, biased, funny, overconfident before kickoff, instantly melting down if Barça drop points, and ruthless when bantering Real Madrid, Atlético Madrid, Man City, Arsenal, and PSG.
- Short, punchy paragraphs separated by blank lines. Sometimes a blunt 1-liner or ALL CAPS burst. Occasionally end a bold claim with "End." for comic finality.
- Make rival fans feel compelled to quote-tweet or argue in the replies by taking strong, specific football stances (stats, trophies, tactics, big-game performances, refereeing decisions, transfers).

STRICT FORMATTING RULES (NEVER VIOLATE):
1. HARD LENGTH LIMIT: Maximum 270 characters total (including spaces and newlines). Shorter (80-220 chars) usually performs better.
2. ZERO HASHTAGS. Never output `#FCBarcelona`, `#Barca`, `#UCL`, or any `#` tag.
3. ZERO URLS OR LINKS. Never output `http://`, `https://`, or `.com`.
4. ZERO EMOJIS (pure text hits much harder on Football Twitter).
5. BANNED AI CLICHÉS: Never write "Hot take:", "Unpopular opinion:", "Let's discuss", "What do you think?", "Drop your thoughts below", "In the world of football", "Visca el Barça!", or corporate sports-journalist phrasing.
6. FACTUAL GROUNDING: Base match reactions, scores, opponents, scorers, managers, and current squads strictly on the provided LIVE FOOTBALL CONTEXT. Never invent a fake match score or mention a former manager as if they are still managing the club.
7. SAFETY & X TOS GUARDRAILS: Keep all trash talk 100% focused on football — tactics, form, missed chances, transfers, club rivalries, PR narratives, and refereeing banter. NEVER use slurs, racism, xenophobia, personal/family attacks, threats, or hate speech.
"""


def choose_archetype(
    match_state: MatchState,
    recent_archetypes: List[str],
    forced_archetype: Optional[str] = None,
    enabled_archetypes: Optional[List[str]] = None,
) -> PostArchetype:
    """Pick the best post archetype for the current match state while respecting Admin toggles."""
    if forced_archetype:
        if forced_archetype not in ARCHETYPES:
            valid = ", ".join(sorted(ARCHETYPES.keys()))
            raise ValueError(f"Unknown archetype '{forced_archetype}'. Valid options: {valid}")
        return ARCHETYPES[forced_archetype]

    allowed = set(enabled_archetypes) if enabled_archetypes else set(ARCHETYPES.keys())
    weighted = STATE_ARCHETYPE_WEIGHTS.get(match_state, STATE_ARCHETYPE_WEIGHTS[MatchState.OFF_DAY_BANTER])
    filtered = [(name, w) for name, w in weighted if name in allowed]
    if not filtered:
        # Fall back to any enabled archetype if admin disabled all state defaults
        fallback_names = [n for n in ARCHETYPES if n in allowed] or list(ARCHETYPES.keys())
        filtered = [(n, 10) for n in fallback_names]

    last_one = recent_archetypes[0] if recent_archetypes else None
    last_two = set(recent_archetypes[:2])

    names: List[str] = []
    weights: List[float] = []
    for name, base_weight in filtered:
        w = float(base_weight)
        if name == last_one and len(filtered) > 1:
            w *= 0.25
        elif name in last_two and len(filtered) > 2:
            w *= 0.6
        names.append(name)
        weights.append(max(1.0, w))

    chosen_name = random.choices(names, weights=weights, k=1)[0]
    return ARCHETYPES[chosen_name]


def build_user_prompt(
    context: LiveFootballContext,
    archetype: PostArchetype,
    image_menu: str,
    recent_posts: List[str],
    encourage_images: bool,
    trigger_type: str = "scheduled",
    chaos_level: str = "spicy",
    custom_directive: str = "",
) -> str:
    """Construct the full generation prompt with live match triggers, chaos tuning, and image menu."""
    recent_block = (
        "\n".join(f"  {i + 1}. {text}" for i, text in enumerate(recent_posts[:12]))
        if recent_posts
        else "  (None yet — this is one of the first posts.)"
    )

    trigger_note = ""
    if trigger_type == "halftime":
        trigger_note = (
            "\n=== INSTANT HALF-TIME TRIGGER ===\n"
            "IT IS HALF-TIME RIGHT NOW in FC Barcelona's match! Everyone is checking X during the 15-minute break. "
            "React immediately to the exact first-half scoreline and scorers from the live context — raw, impulsive half-time Football Twitter energy!"
        )
    elif trigger_type == "fulltime":
        trigger_note = (
            "\n=== INSTANT FULL-TIME TRIGGER ===\n"
            "THE FINAL WHISTLE JUST BLEW in FC Barcelona's match! Views peak right now. "
            "React immediately to the exact final scoreline, scorers, and breaking post-match news above — "
            "pure euphoria if Barça won, or dramatic comedic meltdown if Barça drew/lost!"
        )

    chaos_note = CHAOS_LEVEL_NOTES.get(chaos_level, CHAOS_LEVEL_NOTES["spicy"])
    directive_block = (
        f"\n=== ADMIN CUSTOM DIRECTIVE (PRIORITIZE IF RELEVANT) ===\n{custom_directive.strip()}"
        if custom_directive.strip()
        else ""
    )

    if encourage_images:
        if archetype.preferred_image_count == 2:
            image_guidance = (
                "IMAGE SELECTION: Strongly prefer selecting 2 contrasting images from the catalog "
                "(e.g., 1 Barça player + 1 rival player) for a side-by-side comparison post. "
                "If only 1 strong match exists, select 1. Never invent IDs not in the catalog."
            )
        else:
            image_guidance = (
                "IMAGE SELECTION: Strongly prefer selecting 1 image from the catalog whose subject and mood "
                "match the exact punchline of your post. Never invent IDs not in the catalog."
            )
    else:
        image_guidance = (
            "IMAGE SELECTION: For this slot, prefer a sharp text-only post (selected_image_ids: []), "
            "UNLESS a 2-image side-by-side comparison makes the joke way funnier."
        )

    return f"""=== LIVE FOOTBALL CONTEXT ===
{context.to_prompt_block()}{trigger_note}

=== TARGET POST ARCHETYPE ===
Archetype: {archetype.name} ({archetype.description})
Chaos Level ({chaos_level}): {chaos_note}
Instructions: {archetype.prompt_instructions}{directive_block}

=== RECENT POSTS (DO NOT REPEAT TOPICS, PLAYERS, OR PHRASING) ===
{recent_block}

=== IMAGE CATALOG ===
{image_menu}
{image_guidance}

Now write ONE high-engagement X post in JSON matching the schema. Keep `text` under 270 characters, zero hashtags, zero URLs, zero emojis, pure Football Twitter energy."""
