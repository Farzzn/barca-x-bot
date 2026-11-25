"""Gemini Interactions API post generator (`google-genai` >= 3.2.0) with dual-key failover.

Features:
  - Dual Gemini API keys (`BARCA_GEMINI_API_KEY` primary -> `BARCA_GEMINI_API_KEY_2` fallback)
  - Automatic model fallback (`gemini-4-hydrogen` -> `gemini-3.5-flash-lite` on 503 demand spikes)
  - Optional Google Search grounding with graceful fallback to free ESPN + RSS context
  - Full telemetry (`gemini_key_used`, `gemini_fallback_reason`, `gemini_model_used`, `search_grounded`)
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from google import genai
from pydantic import BaseModel, Field

from app.config import RuntimeBehaviorConfig, Settings
from app.context_fetcher import LiveFootballContext
from app.image_catalog import ImageAsset, ImageCatalog
from app.persona import (
    SYSTEM_INSTRUCTION,
    PostArchetype,
    build_user_prompt,
    choose_archetype,
)

logger = logging.getLogger(__name__)

MAX_TWEET_CHARS = 280
FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
URL_PATTERN = re.compile(r"(https?://\S+|www\.\S+|\b[a-zA-Z0-9-]+\.(com|net|org|io|co|uk|es)\b)", re.IGNORECASE)
HASHTAG_PATTERN = re.compile(r"#\w+")


class GeneratedPost(BaseModel):
    """Structured output returned by Gemini."""

    text: str = Field(
        description="The exact X post text. Strictly <= 270 characters, zero hashtags, zero URLs, zero emojis."
    )
    selected_image_ids: List[str] = Field(
        default_factory=list,
        description="0, 1, or 2 exact image IDs copied verbatim from the provided image catalog.",
    )
    angle_summary: str = Field(
        description="Short 5-10 word summary of the banter angle/topic used (for logging and anti-repetition)."
    )


@dataclass
class GenerationResult:
    """Generated post bundled with full API key & model telemetry for the Admin UI."""

    post: GeneratedPost
    archetype: PostArchetype
    resolved_images: List[ImageAsset]
    gemini_key_used: str  # "primary" | "fallback"
    gemini_fallback_reason: str
    gemini_model_used: str
    search_grounded: bool


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def is_too_similar(candidate: str, recent_posts: List[str], threshold: float = 0.65) -> bool:
    """Reject near-duplicate posts using Jaccard word overlap."""
    cand_words = _word_set(candidate)
    if not cand_words:
        return True
    for old in recent_posts:
        old_words = _word_set(old)
        if not old_words:
            continue
        overlap = len(cand_words & old_words) / len(cand_words | old_words)
        if overlap >= threshold:
            return True
    return False


def sanitize_and_validate_text(raw_text: str) -> Tuple[str, List[str]]:
    """Clean minor whitespace issues and return `(cleaned_text, validation_errors)`."""
    text = raw_text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) >= 2 and ((text[0] == '"' and text[-1] == '"') or (text[0] == "'" and text[-1] == "'")):
        text = text[1:-1].strip()

    errors: List[str] = []
    if not text:
        errors.append("Post text is empty.")
    if len(text) > MAX_TWEET_CHARS:
        errors.append(f"Post text is {len(text)} chars (exceeds X limit of {MAX_TWEET_CHARS}).")
    if URL_PATTERN.search(text):
        errors.append("Post contains a URL (forbidden to avoid X's URL surcharge & reach penalty).")
    if HASHTAG_PATTERN.search(text):
        errors.append("Post contains a hashtag (forbidden for authentic Football Twitter style).")

    return text, errors


def _short_error(exc: Exception) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    return msg[:220]


class PostGenerator:
    """Generates banter posts using primary + fallback Gemini API keys with telemetry."""

    def __init__(
        self,
        settings: Settings,
        primary_client: Optional[genai.Client] = None,
        fallback_client: Optional[genai.Client] = None,
    ) -> None:
        self.settings = settings
        self._clients: List[Tuple[str, genai.Client]] = []

        if primary_client is not None:
            self._clients.append(("primary", primary_client))
        elif settings.gemini_api_key:
            self._clients.append(
                ("primary", genai.Client(api_key=settings.gemini_api_key, http_options={"timeout": 25000}))
            )

        if fallback_client is not None:
            self._clients.append(("fallback", fallback_client))
        elif settings.gemini_api_key_fallback:
            self._clients.append(
                ("fallback", genai.Client(api_key=settings.gemini_api_key_fallback, http_options={"timeout": 25000}))
            )

        if not self._clients:
            raise ValueError(
                "No Gemini API key configured. Set BARCA_GEMINI_API_KEY (and optional BARCA_GEMINI_API_KEY_2) "
                "in `barca-x-bot/.env` or Heroku Config Vars."
            )

    def _call_single_client(
        self,
        client: genai.Client,
        model: str,
        prompt: str,
        use_google_search: bool,
    ) -> GeneratedPost:
        kwargs = {
            "model": model,
            "input": prompt,
            "system_instruction": SYSTEM_INSTRUCTION,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": GeneratedPost.model_json_schema(),
            },
            "generation_config": {
                "temperature": 1.0,
            },
            "store": False,
        }
        if use_google_search:
            kwargs["tools"] = [{"type": "google_search"}]

        interaction = client.interactions.create(**kwargs)
        raw_output = interaction.output_text
        if not raw_output:
            raise RuntimeError("Gemini returned an empty interaction.output_text.")
        return GeneratedPost.model_validate_json(raw_output)

    def _invoke_with_failover(
        self,
        prompt: str,
        preferred_model: str,
        enable_google_search: bool,
    ) -> Tuple[GeneratedPost, str, str, str, bool]:
        """Try Primary key -> Fallback key (with Search + 503 model fallbacks).

        Returns `(post, key_used, fallback_reason, model_used, search_grounded)`.
        """
        primary_failure_reason = ""
        models_to_try = [preferred_model]
        for fallback_model in FALLBACK_MODELS:
            if fallback_model not in models_to_try:
                models_to_try.append(fallback_model)

        last_exc: Optional[Exception] = None

        for key_label, client in self._clients:
            for model_name in models_to_try:
                # 1) Try with Google Search grounding if enabled
                if enable_google_search:
                    try:
                        post = self._call_single_client(client, model_name, prompt, use_google_search=True)
                        return post, key_label, primary_failure_reason, model_name, True
                    except Exception as exc:
                        logger.warning(
                            "Gemini [%s / %s + google_search] failed (%s); retrying without google_search tool.",
                            key_label,
                            model_name,
                            _short_error(exc),
                        )

                # 2) Try without Google Search grounding (still grounded by ESPN + RSS!)
                try:
                    post = self._call_single_client(client, model_name, prompt, use_google_search=False)
                    return post, key_label, primary_failure_reason, model_name, False
                except Exception as exc:
                    last_exc = exc
                    err_str = _short_error(exc)
                    logger.warning("Gemini [%s / %s] failed: %s", key_label, model_name, err_str)
                    if key_label == "primary" and not primary_failure_reason:
                        primary_failure_reason = err_str
                    # If 503 / high demand / not found, try next model on same key;
                    # if 429 quota / 401 / 403 auth, immediately fail over to the fallback key!
                    low = err_str.lower()
                    if any(tok in low for tok in ("429", "quota", "rate", "401", "403", "api_key", "permission")):
                        break

        raise RuntimeError(f"All Gemini API keys/models failed. Last error: {last_exc}")

    def generate(
        self,
        context: LiveFootballContext,
        catalog: ImageCatalog,
        recent_posts: List[str],
        recent_archetypes: List[str],
        behavior: Optional[RuntimeBehaviorConfig] = None,
        trigger_type: str = "scheduled",
        forced_archetype: Optional[str] = None,
        force_images: Optional[bool] = None,
    ) -> GenerationResult:
        """Generate a validated post with dual-key failover and return full telemetry."""
        cfg = behavior or self.settings.default_behavior()

        archetype = choose_archetype(
            match_state=context.match_state,
            recent_archetypes=recent_archetypes,
            forced_archetype=forced_archetype,
            enabled_archetypes=cfg.enabled_archetypes,
        )

        has_images = bool(catalog.assets)
        if force_images is not None:
            encourage_images = force_images and has_images
        else:
            encourage_images = has_images and (
                archetype.preferred_image_count == 2 or random.random() < cfg.image_post_ratio
            )

        base_prompt = build_user_prompt(
            context=context,
            archetype=archetype,
            image_menu=catalog.to_prompt_menu(),
            recent_posts=recent_posts,
            encourage_images=encourage_images,
            trigger_type=trigger_type,
            chaos_level=cfg.chaos_level,
            custom_directive=cfg.custom_directive,
        )

        prompt = base_prompt
        last_errors: List[str] = []
        post: Optional[GeneratedPost] = None
        key_used = "primary"
        fallback_reason = ""
        model_used = cfg.gemini_model
        search_grounded = False

        for attempt in range(1, 3):
            post, key_used, fallback_reason, model_used, search_grounded = self._invoke_with_failover(
                prompt=prompt,
                preferred_model=cfg.gemini_model,
                enable_google_search=cfg.enable_google_search,
            )

            cleaned_text, errors = sanitize_and_validate_text(post.text)
            if not errors and is_too_similar(cleaned_text, recent_posts):
                errors.append("Post is too similar to a recent post; pick a fresh player, rival, or punchline.")

            if not errors:
                post.text = cleaned_text
                resolved_images = catalog.resolve_ids(post.selected_image_ids, max_images=2)
                post.selected_image_ids = [img.id for img in resolved_images]
                return GenerationResult(
                    post=post,
                    archetype=archetype,
                    resolved_images=resolved_images,
                    gemini_key_used=key_used,
                    gemini_fallback_reason=fallback_reason,
                    gemini_model_used=model_used,
                    search_grounded=search_grounded,
                )

            last_errors = errors
            logger.warning("Attempt %d validation issues: %s", attempt, "; ".join(errors))
            prompt = (
                f"{base_prompt}\n\n"
                f"PREVIOUS ATTEMPT FAILED VALIDATION ({'; '.join(errors)}). "
                "Fix every issue strictly: keep under 265 chars, NO hashtags, NO URLs, completely fresh angle."
            )

        assert post is not None
        cleaned_text = URL_PATTERN.sub("", post.text)
        cleaned_text = HASHTAG_PATTERN.sub("", cleaned_text).strip()
        if len(cleaned_text) > MAX_TWEET_CHARS:
            cleaned_text = cleaned_text[: MAX_TWEET_CHARS - 1].rstrip() + "."
        if not cleaned_text:
            raise RuntimeError(f"Failed to generate a valid post after retries: {last_errors}")

        post.text = cleaned_text
        resolved_images = catalog.resolve_ids(post.selected_image_ids, max_images=2)
        post.selected_image_ids = [img.id for img in resolved_images]
        return GenerationResult(
            post=post,
            archetype=archetype,
            resolved_images=resolved_images,
            gemini_key_used=key_used,
            gemini_fallback_reason=fallback_reason,
            gemini_model_used=model_used,
            search_grounded=search_grounded,
        )
