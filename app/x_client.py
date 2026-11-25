"""Native X (Twitter) API v2 client using OAuth 1.0a User Context.

Uses the modern v2 endpoints (never deprecated v1.1 media upload):
  - POST https://api.x.com/2/media/upload/initialize
  - POST https://api.x.com/2/media/upload/{id}/append
  - POST https://api.x.com/2/media/upload/{id}/finalize
  - POST https://api.x.com/2/tweets
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import requests
from requests_oauthlib import OAuth1

from app.config import Settings
from app.image_catalog import prepare_image_for_x

logger = logging.getLogger(__name__)

X_API_BASE = "https://api.x.com/2"
CHUNK_SIZE_BYTES = 4 * 1024 * 1024  # 4 MB segments (safely under X's 5 MB max)
HTTP_TIMEOUT = 30


class XAPIError(RuntimeError):
    """Raised when X API v2 rejects a media upload or post request."""

    def __init__(self, status_code: int, message: str, raw_body: str = "") -> None:
        self.status_code = status_code
        self.raw_body = raw_body
        super().__init__(message)


@dataclass
class XPostResult:
    tweet_id: str
    url: str
    text: str
    media_ids: List[str] = field(default_factory=list)


def _format_api_error(action: str, resp: requests.Response) -> XAPIError:
    status = resp.status_code
    body = resp.text[:500]
    hint = ""
    if status == 401:
        hint = " Check X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, and X_ACCESS_TOKEN_SECRET."
    elif status == 402:
        hint = " Your X API pay-per-use credit balance may be empty. Check developer.x.com."
    elif status == 403:
        hint = (
            " Ensure your X App has 'Read and Write' permissions under User Authentication Settings, "
            "and REGENERATE your Access Token & Secret after changing permissions."
        )
    elif status == 429:
        hint = " X API rate limit reached; will try again on the next scheduled run."
    return XAPIError(status, f"X API {action} failed [{status}]: {body}{hint}", raw_body=body)


class XClient:
    """Minimal, reliable X API v2 poster with native v2 chunked media upload."""

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None) -> None:
        if not settings.has_x_credentials:
            raise ValueError(
                "Missing X API credentials. Set X_API_KEY, X_API_SECRET, "
                "X_ACCESS_TOKEN, and X_ACCESS_TOKEN_SECRET."
            )
        self.auth = OAuth1(
            client_key=settings.x_api_key,
            client_secret=settings.x_api_secret,
            resource_owner_key=settings.x_access_token,
            resource_owner_secret=settings.x_access_token_secret,
        )
        self.session = session or requests.Session()

    def upload_image(self, image_path: Path) -> str:
        """Upload a single image via X API v2 initialize/append/finalize and return media_id."""
        raw_bytes, mime_type, filename = prepare_image_for_x(image_path)
        total_bytes = len(raw_bytes)

        # 1. INITIALIZE
        init_url = f"{X_API_BASE}/media/upload/initialize"
        init_payload = {
            "media_type": mime_type,
            "total_bytes": total_bytes,
            "media_category": "tweet_image",
        }
        init_resp = self.session.post(
            init_url,
            auth=self.auth,
            json=init_payload,
            timeout=HTTP_TIMEOUT,
        )
        if init_resp.status_code not in {200, 201}:
            raise _format_api_error("media initialize", init_resp)

        init_json = init_resp.json()
        media_id = str(init_json.get("data", init_json).get("id", "")).strip()
        if not media_id:
            raise XAPIError(init_resp.status_code, f"X API media initialize returned no id: {init_json}")

        # 2. APPEND (chunked)
        append_url = f"{X_API_BASE}/media/upload/{media_id}/append"
        segment_index = 0
        for offset in range(0, total_bytes, CHUNK_SIZE_BYTES):
            chunk = raw_bytes[offset : offset + CHUNK_SIZE_BYTES]
            append_resp = self.session.post(
                append_url,
                auth=self.auth,
                data={"segment_index": str(segment_index)},
                files={"media": (filename, chunk, mime_type)},
                timeout=HTTP_TIMEOUT,
            )
            if append_resp.status_code not in {200, 204}:
                raise _format_api_error(f"media append (segment {segment_index})", append_resp)
            segment_index += 1

        # 3. FINALIZE
        finalize_url = f"{X_API_BASE}/media/upload/{media_id}/finalize"
        finalize_resp = self.session.post(
            finalize_url,
            auth=self.auth,
            timeout=HTTP_TIMEOUT,
        )
        if finalize_resp.status_code not in {200, 201}:
            raise _format_api_error("media finalize", finalize_resp)

        logger.info("Uploaded image %s -> media_id=%s (%d bytes)", filename, media_id, total_bytes)
        return media_id

    def create_post(self, text: str, image_paths: Optional[List[Path]] = None) -> XPostResult:
        """Create an X post with 0-4 uploaded images (2 images render side-by-side)."""
        media_ids: List[str] = []
        for path in (image_paths or [])[:4]:
            media_ids.append(self.upload_image(path))

        payload = {"text": text}
        if media_ids:
            payload["media"] = {"media_ids": media_ids}

        resp = self.session.post(
            f"{X_API_BASE}/tweets",
            auth=self.auth,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code not in {200, 201}:
            raise _format_api_error("create tweet", resp)

        data = resp.json().get("data", {})
        tweet_id = str(data.get("id", "")).strip()
        url = f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""
        return XPostResult(tweet_id=tweet_id, url=url, text=text, media_ids=media_ids)
