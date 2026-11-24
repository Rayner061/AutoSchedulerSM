"""Downloading media from Google Drive or a web link, and preparing images."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from autopost.models import Media, PublishError

DRIVE_API = "https://www.googleapis.com/drive/v3/files"
CHUNK = 1024 * 1024
MAX_DOWNLOAD_BYTES = 4 * 1024**3  # TikTok's own upper limit is 4 GB

EXTRA_TYPES = {".mov": "video/quicktime", ".m4v": "video/mp4", ".heic": "image/heic", ".webp": "image/webp"}


def drive_file_id(ref: str) -> Optional[str]:
    """Return the Drive file ID for a Drive link (or a bare ID), else None."""
    ref = ref.strip()
    if not ref:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{25,}", ref):
        return ref
    parsed = urlparse(ref)
    if "drive.google.com" not in parsed.netloc and "docs.google.com" not in parsed.netloc:
        return None
    match = re.search(r"/d/([A-Za-z0-9_-]+)", parsed.path)
    if match:
        return match.group(1)
    ids = parse_qs(parsed.query).get("id")
    return ids[0] if ids else None


def guess_type(name: str, header_type: str = "") -> str:
    header_type = (header_type or "").split(";")[0].strip().lower()
    if header_type.startswith(("image/", "video/")):
        return header_type
    suffix = Path(name).suffix.lower()
    if suffix in EXTRA_TYPES:
        return EXTRA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(name)
    return guessed or header_type or "application/octet-stream"


def _safe_name(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned[:120] or fallback


def _stream_to(resp: requests.Response, dest: Path) -> int:
    total = 0
    with dest.open("wb") as fh:
        for chunk in resp.iter_content(CHUNK):
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise PublishError("Media file is larger than 4 GB.")
            fh.write(chunk)
    return total


def download(ref: str, workdir: Path, drive_session=None) -> Media:
    """Download a Drive file or a direct web link into workdir."""
    workdir.mkdir(parents=True, exist_ok=True)
    file_id = drive_file_id(ref)
    if file_id:
        if drive_session is None:
            raise PublishError("Google Drive access is not configured.")
        meta = drive_session.get(
            f"{DRIVE_API}/{file_id}",
            params={"fields": "name,mimeType,size", "supportsAllDrives": "true"},
            timeout=60,
        )
        if meta.status_code == 404:
            raise PublishError(
                "Drive file not found. Share the file (or its folder) with the service account email."
            )
        if meta.status_code >= 400:
            raise PublishError(f"Google Drive error {meta.status_code}: {meta.text[:200]}")
        info = meta.json()
        mime = info.get("mimeType", "")
        if mime.startswith("application/vnd.google-apps"):
            raise PublishError("That Drive link is a Google Doc/Sheet/folder, not a photo or video file.")
        name = _safe_name(info.get("name", ""), file_id)
        dest = workdir / name
        resp = drive_session.get(
            f"{DRIVE_API}/{file_id}",
            params={"alt": "media", "supportsAllDrives": "true"},
            stream=True,
            timeout=300,
        )
        if resp.status_code >= 400:
            raise PublishError(f"Google Drive download failed ({resp.status_code}).")
        size = _stream_to(resp, dest)
        return Media(path=dest, mime_type=guess_type(name, mime), size=size, name=name)

    parsed = urlparse(ref)
    if parsed.scheme not in {"http", "https"}:
        raise PublishError("Media must be a Google Drive link or a direct http(s) link to a file.")
    try:
        resp = requests.get(ref, stream=True, timeout=300, headers={"User-Agent": "autopost/1.0"})
    except requests.RequestException as exc:
        raise PublishError(f"Could not download media: {exc.__class__.__name__}") from exc
    if resp.status_code >= 400:
        raise PublishError(f"Media link returned HTTP {resp.status_code}.")
    name = _safe_name(Path(parsed.path).name, "media")
    mime = guess_type(name, resp.headers.get("Content-Type", ""))
    if "." not in name:
        ext = mimetypes.guess_extension(mime) or ""
        name += ext
    dest = workdir / name
    size = _stream_to(resp, dest)
    if mime.startswith("text/html"):
        raise PublishError(
            "The media link opened a web page, not a file. Use a direct file link or a Google Drive link."
        )
    return Media(path=dest, mime_type=mime, size=size, name=name)


def to_jpeg(media: Media) -> Path:
    """Instagram only accepts JPEG photos; convert anything else (PNG, HEIC, WebP...)."""
    from PIL import Image, ImageOps

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    out = media.path.with_name(media.path.stem + "_ig.jpg")
    try:
        with Image.open(media.path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.save(out, "JPEG", quality=95)
    except Exception as exc:
        raise PublishError(f"Could not read the image file ({exc.__class__.__name__}).") from exc
    return out
