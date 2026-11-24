"""TikTok Content Posting API: upload to inbox (draft) or post directly."""

from __future__ import annotations

import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import requests

from autopost.models import INBOX, PROCESSING, Post, PublishError, Result

API = "https://open.tiktokapis.com"
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.upload,video.publish"

MB = 1024 * 1024
SINGLE_CHUNK_LIMIT = 64 * MB
CHUNK_SIZE = 32 * MB


def chunk_plan(size: int) -> tuple[int, int]:
    """Return (chunk_size, chunk_count) following TikTok's rules: files up to 64 MB go in one
    piece; bigger files use fixed chunks and the last chunk absorbs the remainder."""
    if size <= 0:
        raise PublishError("The video file is empty.")
    if size <= SINGLE_CHUNK_LIMIT:
        return size, 1
    return CHUNK_SIZE, size // CHUNK_SIZE


def login_url(client_key: str, redirect_uri: str, scopes: str = SCOPES) -> str:
    query = urlencode(
        {
            "client_key": client_key,
            "scope": scopes,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": secrets.token_urlsafe(12),
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _token_request(data: dict, session: Optional[requests.Session] = None) -> dict:
    http = session or requests
    try:
        resp = http.post(
            f"{API}/v2/oauth/token/",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise PublishError(f"Network error talking to TikTok: {exc.__class__.__name__}") from exc
    try:
        body = resp.json()
    except ValueError:
        raise PublishError(f"TikTok token endpoint returned HTTP {resp.status_code}") from None
    if "access_token" not in body:
        desc = body.get("error_description") or body.get("error") or resp.text[:200]
        raise PublishError(
            f"TikTok login failed: {desc}. If this keeps happening, reconnect TikTok (see the guide)."
        )
    return body


def exchange_code(client_key: str, client_secret: str, code: str, redirect_uri: str,
                  session: Optional[requests.Session] = None) -> dict:
    return _token_request(
        {
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        session,
    )


def refresh(client_key: str, client_secret: str, refresh_token: str,
            session: Optional[requests.Session] = None) -> dict:
    return _token_request(
        {
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        session,
    )


class TikTokClient:
    def __init__(self, access_token: str, mode: str = "inbox", privacy: str = "SELF_ONLY",
                 session: Optional[requests.Session] = None, poll_seconds: float = 10,
                 max_wait_seconds: float = 600):
        self.token = access_token
        self.mode = mode
        self.privacy = privacy
        self.http = session or requests.Session()
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds

    def _post(self, path: str, payload: Optional[dict] = None) -> dict:
        try:
            resp = self.http.post(
                f"{API}{path}",
                json=payload if payload is not None else {},
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            raise PublishError(f"Network error talking to TikTok: {exc.__class__.__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            raise PublishError(f"TikTok returned HTTP {resp.status_code}") from None
        err = body.get("error") or {}
        if err.get("code", "ok") != "ok" or resp.status_code >= 400:
            code = err.get("code", resp.status_code)
            message = err.get("message", "")
            hints = {
                "unaudited_client_can_only_post_to_private_accounts": (
                    " Until TikTok approves (audits) your app, set TIKTOK_PRIVACY to SELF_ONLY "
                    "or use TIKTOK_MODE=inbox."
                ),
                "spam_risk_too_many_posts": " TikTok's daily posting limit was reached; will retry.",
                "scope_not_authorized": " Reconnect TikTok and allow all requested permissions.",
                "access_token_invalid": " Reconnect TikTok (see the guide).",
            }
            raise PublishError(f"TikTok error {code}: {message}{hints.get(code, '')}".strip())
        return body.get("data") or {}

    def user_info(self) -> dict:
        try:
            resp = self.http.get(
                f"{API}/v2/user/info/",
                params={"fields": "open_id,display_name"},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60,
            )
            return (resp.json().get("data") or {}).get("user") or {}
        except (requests.RequestException, ValueError):
            return {}

    def creator_info(self) -> dict:
        return self._post("/v2/post/publish/creator_info/query/")

    def publish(self, post: Post) -> Result:
        media = post.media
        if media is None or not media.is_video:
            return Result.skipped("TikTok posting here supports videos only")

        chunk_size, chunk_count = chunk_plan(media.size)
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": media.size,
            "chunk_size": chunk_size,
            "total_chunk_count": chunk_count,
        }
        username = ""
        if self.mode == "direct":
            creator = self.creator_info()
            username = creator.get("creator_username", "")
            options = creator.get("privacy_level_options") or []
            if options and self.privacy not in options:
                raise PublishError(
                    f"TikTok does not allow privacy '{self.privacy}' for this account right now. "
                    f"Allowed: {', '.join(options)}. Change TIKTOK_PRIVACY."
                )
            payload = {
                "post_info": {
                    "title": post.caption_for("tiktok")[:2200],
                    "privacy_level": self.privacy,
                    "disable_comment": bool(creator.get("comment_disabled")),
                    "disable_duet": bool(creator.get("duet_disabled")),
                    "disable_stitch": bool(creator.get("stitch_disabled")),
                },
                "source_info": source_info,
            }
            data = self._post("/v2/post/publish/video/init/", payload)
        else:
            data = self._post("/v2/post/publish/inbox/video/init/", {"source_info": source_info})

        publish_id = data["publish_id"]
        self._upload(data["upload_url"], media, chunk_size, chunk_count)
        return self._wait(publish_id, username)

    def _upload(self, upload_url: str, media, chunk_size: int, chunk_count: int) -> None:
        mime = media.mime_type if media.mime_type in ("video/mp4", "video/quicktime", "video/webm") else "video/mp4"
        with media.path.open("rb") as fh:
            for index in range(chunk_count):
                start = index * chunk_size
                end = media.size - 1 if index == chunk_count - 1 else start + chunk_size - 1
                fh.seek(start)
                body = fh.read(end - start + 1)
                try:
                    resp = self.http.put(
                        upload_url,
                        data=body,
                        headers={
                            "Content-Type": mime,
                            "Content-Length": str(len(body)),
                            "Content-Range": f"bytes {start}-{end}/{media.size}",
                        },
                        timeout=600,
                    )
                except requests.RequestException as exc:
                    raise PublishError(f"TikTok upload failed: {exc.__class__.__name__}") from exc
                if resp.status_code not in (200, 201, 206):
                    raise PublishError(f"TikTok upload failed on part {index + 1}: HTTP {resp.status_code}")

    def _wait(self, publish_id: str, username: str) -> Result:
        deadline = time.monotonic() + self.max_wait_seconds
        while True:
            data = self._post("/v2/post/publish/status/fetch/", {"publish_id": publish_id})
            status = data.get("status", "")
            if status == "FAILED":
                raise PublishError(f"TikTok rejected the video: {data.get('fail_reason', 'unknown reason')}")
            if status == "SEND_TO_USER_INBOX":
                return Result(INBOX, "Sent to your TikTok inbox - open TikTok, add the caption and tap Post")
            if status == "PUBLISH_COMPLETE":
                ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
                if ids and username:
                    return Result.success(f"https://www.tiktok.com/@{username}/video/{ids[0]}")
                if self.privacy == "SELF_ONLY":
                    return Result.success("Posted as private (only you can see it until TikTok audits your app)")
                return Result.success(f"Posted (publish id {publish_id})")
            if time.monotonic() >= deadline:
                return Result(PROCESSING, f"Uploaded; TikTok is still processing (publish id {publish_id})")
            time.sleep(self.poll_seconds)
