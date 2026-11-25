"""Curated local + GitHub-synced image catalog & X API v2 image preparation.

Images live in `assets/images/{barca,rivals,managers,memes}/`.
When `GITHUB_REPO` is configured (and images are uploaded from the Vercel Admin UI),
this catalog automatically reads the live GitHub repository tree & `assets/catalog.json`
and downloads selected images on demand — ZERO Heroku redeploy needed!
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from PIL import Image

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_X_IMAGE_BYTES = 4_800_000
MAX_IMAGE_DIMENSION = 2048
HTTP_TIMEOUT = 15

MOOD_KEYWORDS = {
    "celebrating",
    "celebration",
    "hype",
    "smiling",
    "smile",
    "laughing",
    "cold",
    "serious",
    "focused",
    "angry",
    "frustrated",
    "shocked",
    "sad",
    "crying",
    "complaining",
    "worried",
    "stressed",
    "arms_up",
    "walking",
    "bench",
    "trophy",
    "injured",
    "pointing",
    "shushing",
}

CATEGORY_CLUB_DEFAULTS = {
    "barca": "FC Barcelona",
    "rivals": "Rival Club",
    "managers": "Manager",
    "memes": "Football Reaction",
}

KNOWN_RIVAL_CLUBS = {
    "vinicius": "Real Madrid",
    "vini": "Real Madrid",
    "mbappe": "Real Madrid",
    "bellingham": "Real Madrid",
    "rodrygo": "Real Madrid",
    "valverde": "Real Madrid",
    "courtois": "Real Madrid",
    "rudiger": "Real Madrid",
    "ancelotti": "Real Madrid",
    "xabi": "Real Madrid",
    "real_madrid": "Real Madrid",
    "madrid": "Real Madrid",
    "simeone": "Atletico Madrid",
    "griezmann": "Atletico Madrid",
    "alvarez": "Atletico Madrid",
    "haaland": "Manchester City",
    "guardiola": "Manchester City",
    "maresca": "Manchester City",
    "foden": "Manchester City",
    "saka": "Arsenal",
    "arteta": "Arsenal",
    "dembele": "PSG",
}


@dataclass
class ImageAsset:
    id: str
    relative_path: str
    category: str
    subject: str
    club: str
    mood: str
    description: str
    tags: List[str] = field(default_factory=list)
    github_download_url: str = ""
    github_repo_path: str = ""

    def absolute_path(self, assets_dir: Path) -> Path:
        return assets_dir / self.relative_path


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def infer_metadata_from_path(rel_path: Path) -> ImageAsset:
    """Infer subject, club, mood, and description from `<category>/<filename>`."""
    category = rel_path.parts[0].lower() if len(rel_path.parts) > 1 else "memes"
    stem = rel_path.stem
    tokens = [t for t in re.split(r"[\s\-_]+", stem.lower()) if t]

    mood_tokens: List[str] = []
    subject_tokens: List[str] = []
    for tok in tokens:
        if tok in MOOD_KEYWORDS or tok.isdigit():
            if tok in MOOD_KEYWORDS:
                mood_tokens.append(tok)
        else:
            subject_tokens.append(tok)

    if not subject_tokens:
        subject_tokens = tokens or ["football"]

    subject = " ".join(w.capitalize() for w in subject_tokens)
    mood = ", ".join(mood_tokens) if mood_tokens else "neutral"

    club = CATEGORY_CLUB_DEFAULTS.get(category, "Football")
    if category in {"rivals", "managers"}:
        joined = "_".join(subject_tokens)
        for key, mapped_club in KNOWN_RIVAL_CLUBS.items():
            if key in joined:
                club = mapped_club
                break
        if category == "managers" and ("flick" in joined or "barca" in joined):
            club = "FC Barcelona"

    asset_id = _slugify(f"{category}_{stem}")
    description = f"{subject} ({club}) — mood/pose: {mood}"
    tags = sorted(set([category, _slugify(club), *subject_tokens, *mood_tokens]))

    return ImageAsset(
        id=asset_id,
        relative_path=rel_path.as_posix(),
        category=category,
        subject=subject,
        club=club,
        mood=mood,
        description=description,
        tags=tags,
    )


class ImageCatalog:
    """Manages local + GitHub-hosted player/rival/meme images."""

    def __init__(
        self,
        assets_dir: Path,
        catalog_path: Path,
        github_repo: str = "",
        github_branch: str = "main",
        github_token: str = "",
        github_images_prefix: str = "assets/images",
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.assets_dir = assets_dir
        self.catalog_path = catalog_path
        self.github_repo = github_repo.strip()
        self.github_branch = github_branch.strip() or "main"
        self.github_token = github_token.strip()
        self.github_images_prefix = github_images_prefix.strip().strip("/") or "assets/images"
        self.cache_dir = cache_dir or (assets_dir.parent / "_github_cache")
        self.assets: Dict[str, ImageAsset] = {}
        self.reload()

    def _github_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "BarcaXBot/1.1",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def _load_github_overrides(self) -> Dict[str, Dict]:
        """Read `assets/catalog.json` directly from GitHub if present."""
        if not self.github_repo:
            return {}
        url = f"https://api.github.com/repos/{self.github_repo}/contents/assets/catalog.json"
        try:
            resp = requests.get(
                url,
                params={"ref": self.github_branch},
                headers=self._github_headers(),
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                return {}
            data = resp.json()
            decoded = base64.b64decode(data.get("content", "")).decode("utf-8")
            parsed = json.loads(decoded)
            return {
                item["relative_path"]: item
                for item in parsed.get("images", [])
                if "relative_path" in item
            }
        except Exception as exc:
            logger.debug("Could not fetch GitHub catalog.json: %s", exc)
            return {}

    def _discover_github_images(self, overrides: Dict[str, Dict]) -> Dict[str, ImageAsset]:
        """Discover all images stored in GitHub under `assets/images/*`."""
        if not self.github_repo:
            return {}
        tree_url = f"https://api.github.com/repos/{self.github_repo}/git/trees/{self.github_branch}"
        try:
            resp = requests.get(
                tree_url,
                params={"recursive": "1"},
                headers=self._github_headers(),
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning("GitHub tree sync returned %s for %s", resp.status_code, self.github_repo)
                return {}
            tree = resp.json().get("tree", [])
        except Exception as exc:
            logger.warning("GitHub image discovery failed: %s", exc)
            return {}

        prefix = f"{self.github_images_prefix}/"
        found: Dict[str, ImageAsset] = {}
        for node in tree:
            if node.get("type") != "blob":
                continue
            repo_path = str(node.get("path") or "")
            if not repo_path.startswith(prefix):
                continue
            rel_str = repo_path[len(prefix) :]
            rel_path = Path(rel_str)
            if rel_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            inferred = infer_metadata_from_path(rel_path)
            override = overrides.get(rel_path.as_posix())
            raw_url = f"https://raw.githubusercontent.com/{self.github_repo}/{self.github_branch}/{repo_path}"
            if override:
                asset = ImageAsset(
                    id=str(override.get("id") or inferred.id),
                    relative_path=rel_path.as_posix(),
                    category=str(override.get("category") or inferred.category),
                    subject=str(override.get("subject") or inferred.subject),
                    club=str(override.get("club") or inferred.club),
                    mood=str(override.get("mood") or inferred.mood),
                    description=str(override.get("description") or inferred.description),
                    tags=list(override.get("tags") or inferred.tags),
                    github_download_url=raw_url,
                    github_repo_path=repo_path,
                )
            else:
                inferred.github_download_url = raw_url
                inferred.github_repo_path = repo_path
                asset = inferred
            found[asset.id] = asset

        return found

    def reload(self) -> List[ImageAsset]:
        """Scan local `assets_dir` and merge live GitHub images if `GITHUB_REPO` is set."""
        manual_overrides: Dict[str, Dict] = {}
        if self.catalog_path.exists():
            try:
                raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                for item in raw.get("images", []):
                    if "relative_path" in item:
                        manual_overrides[item["relative_path"]] = item
            except Exception as exc:
                logger.warning("Could not read %s: %s", self.catalog_path, exc)

        discovered: Dict[str, ImageAsset] = {}
        if self.assets_dir.exists():
            for path in sorted(self.assets_dir.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                rel = path.relative_to(self.assets_dir)
                rel_posix = rel.as_posix()
                inferred = infer_metadata_from_path(rel)
                override = manual_overrides.get(rel_posix)
                if override:
                    asset = ImageAsset(
                        id=str(override.get("id") or inferred.id),
                        relative_path=rel_posix,
                        category=str(override.get("category") or inferred.category),
                        subject=str(override.get("subject") or inferred.subject),
                        club=str(override.get("club") or inferred.club),
                        mood=str(override.get("mood") or inferred.mood),
                        description=str(override.get("description") or inferred.description),
                        tags=list(override.get("tags") or inferred.tags),
                    )
                else:
                    asset = inferred
                discovered[asset.id] = asset

        # Merge live images from GitHub (overrides/adds newly uploaded Vercel Admin UI images!)
        if self.github_repo:
            gh_overrides = self._load_github_overrides()
            merged_overrides = {**manual_overrides, **gh_overrides}
            gh_assets = self._discover_github_images(merged_overrides)
            discovered.update(gh_assets)

        self.assets = discovered
        return list(self.assets.values())

    def ensure_local_file(self, asset: ImageAsset) -> Optional[Path]:
        """Return a local file path for `asset`, downloading from GitHub into cache if needed."""
        local_path = asset.absolute_path(self.assets_dir)
        if local_path.exists():
            return local_path

        if not self.github_repo or not asset.github_repo_path:
            return None

        cached_path = self.cache_dir / asset.relative_path
        if cached_path.exists() and cached_path.stat().st_size > 0:
            return cached_path

        cached_path.parent.mkdir(parents=True, exist_ok=True)
        # Use GitHub Contents API with raw media type (works for BOTH public and private repos!)
        api_url = f"https://api.github.com/repos/{self.github_repo}/contents/{asset.github_repo_path}"
        headers = {**self._github_headers(), "Accept": "application/vnd.github.raw+json"}
        try:
            resp = requests.get(api_url, params={"ref": self.github_branch}, headers=headers, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            cached_path.write_bytes(resp.content)
            logger.info("Downloaded GitHub image %s -> %s", asset.github_repo_path, cached_path)
            return cached_path
        except Exception as exc:
            logger.error("Failed to download GitHub image %s: %s", asset.github_repo_path, exc)
            return None

    def sync_catalog_file(self) -> Path:
        """Write current merged catalog to `assets/catalog.json` for easy editing."""
        self.reload()
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        images_clean = []
        for asset in self.assets.values():
            d = asdict(asset)
            d.pop("github_download_url", None)
            d.pop("github_repo_path", None)
            images_clean.append(d)
        payload = {
            "note": "Auto-synced from assets/images/. Edit subject/club/mood/description to improve LLM image matching.",
            "images": images_clean,
        }
        self.catalog_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.catalog_path

    def get(self, image_id: str) -> Optional[ImageAsset]:
        return self.assets.get(image_id)

    def resolve_ids(self, image_ids: List[str], max_images: int = 2) -> List[ImageAsset]:
        resolved: List[ImageAsset] = []
        seen = set()
        for img_id in image_ids:
            if img_id in seen:
                continue
            asset = self.assets.get(img_id)
            if asset and (asset.absolute_path(self.assets_dir).exists() or bool(asset.github_repo_path)):
                resolved.append(asset)
                seen.add(img_id)
            if len(resolved) >= max_images:
                break
        return resolved

    def to_prompt_menu(self) -> str:
        """Format available images compactly so Gemini can pick exact IDs."""
        if not self.assets:
            return (
                "No local images are currently available in assets/images/. "
                "Return an empty list [] for selected_image_ids."
            )
        lines = [
            "Available images (pick ONLY exact IDs from this list — 0, 1, or 2 IDs max):"
        ]
        for asset in self.assets.values():
            lines.append(f"  - id=\"{asset.id}\" | {asset.description} | category={asset.category}")
        return "\n".join(lines)


def prepare_image_for_x(image_path: Path) -> Tuple[bytes, str, str]:
    """Validate and normalize an image into X API v2 compatible bytes."""
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    raw_bytes = image_path.read_bytes()
    ext = image_path.suffix.lower()

    if ext in {".jpg", ".jpeg", ".png"} and len(raw_bytes) <= MAX_X_IMAGE_BYTES:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            if max(img.size) <= MAX_IMAGE_DIMENSION and img.mode in {"RGB", "RGBA", "L"}:
                mime = "image/png" if ext == ".png" else "image/jpeg"
                return raw_bytes, mime, image_path.name

    with Image.open(image_path) as img:
        if img.mode in {"RGBA", "P", "LA"}:
            background = Image.new("RGB", img.size, (255, 255, 255))
            converted = img.convert("RGBA")
            background.paste(converted, mask=converted.split()[-1])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        if max(img.size) > MAX_IMAGE_DIMENSION:
            img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)

        for quality in (92, 85, 78, 70):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= MAX_X_IMAGE_BYTES:
                return data, "image/jpeg", f"{image_path.stem}.jpg"

        raise ValueError(f"Could not compress {image_path} under {MAX_X_IMAGE_BYTES} bytes.")
