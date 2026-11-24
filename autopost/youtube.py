"""YouTube uploads through the YouTube Data API v3 (resumable upload)."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlencode

import requests

from autopost.models import Post, PublishError, Result

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000


def login_url(client_id: str, redirect_uri: str) -> str:
    return AUTH_URL + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "access_type": "offline",
            "prompt": "consent",
        }
    )


def _token_request(data: dict, session=None) -> dict:
    http = session or requests
    try:
        resp = http.post(TOKEN_URL, data=data, timeout=60)
    except requests.RequestException as exc:
        raise PublishError(f"Network error talking to Google: {exc.__class__.__name__}") from exc
    try:
        body = resp.json()
    except ValueError:
        raise PublishError(f"Google token endpoint returned HTTP {resp.status_code}") from None
    if "access_token" not in body:
        desc = body.get("error_description") or body.get("error") or resp.text[:200]
        hint = ""
        if body.get("error") == "invalid_grant":
            hint = (" The YouTube login expired or was revoked. Make sure the Google OAuth app is "
                    "'In production' (not 'Testing'), then reconnect YouTube.")
        raise PublishError(f"YouTube login failed: {desc}.{hint}")
    return body


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, session=None) -> dict:
    return _token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        session,
    )


def refresh(client_id: str, client_secret: str, refresh_token: str, session=None) -> dict:
    return _token_request(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        session,
    )


def make_title(post: Post) -> str:
    """YouTube needs a title: use the Title column, else the first line of the caption."""
    title = post.title or next((line for line in post.caption_for("youtube").splitlines() if line.strip()), "")
    title = re.sub(r"[<>]", "", title).strip() or "New video"
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1].rstrip() + "…"
    return title


def make_description(post: Post) -> str:
    text = re.sub(r"[<>]", "", post.caption_for("youtube"))
    encoded = text.encode("utf-8")[:DESCRIPTION_MAX_BYTES]
    return encoded.decode("utf-8", errors="ignore")


def hashtags(text: str) -> list[str]:
    return list(dict.fromkeys(tag[1:] for tag in re.findall(r"#\w+", text)))[:15]


class YouTubeClient:
    def __init__(self, access_token: str, privacy: str = "public", category_id: str = "22",
                 made_for_kids: bool = False, session: Optional[requests.Session] = None):
        self.token = access_token
        self.privacy = privacy
        self.category_id = category_id
        self.made_for_kids = made_for_kids
        self.http = session or requests.Session()

    def _check(self, resp: requests.Response) -> dict:
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            reason = ""
            if err.get("errors"):
                reason = err["errors"][0].get("reason", "")
            message = err.get("message") or resp.text[:300]
            hints = {
                "quotaExceeded": " YouTube's daily upload quota is used up; will retry later.",
                "uploadLimitExceeded": " This channel hit YouTube's upload limit; will retry later.",
                "youtubeSignupRequired": " This Google account has no YouTube channel yet - create one first.",
                "forbidden": " Reconnect YouTube and allow all permissions.",
                "authError": " Reconnect YouTube.",
            }
            raise PublishError(f"YouTube error {resp.status_code} {reason}: {message}{hints.get(reason, '')}".strip())
        return body if isinstance(body, dict) else {}

    def channel(self) -> dict:
        resp = self.http.get(
            f"{API}/channels",
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=60,
        )
        items = self._check(resp).get("items") or []
        return items[0] if items else {}

    def publish(self, post: Post) -> Result:
        media = post.media
        if media is None or not media.is_video:
            return Result.skipped("YouTube needs a video")

        description = make_description(post)
        metadata = {
            "snippet": {
                "title": make_title(post),
                "description": description,
                "categoryId": self.category_id,
            },
            "status": {
                "privacyStatus": self.privacy,
                "selfDeclaredMadeForKids": self.made_for_kids,
            },
        }
        tags = hashtags(description)
        if tags:
            metadata["snippet"]["tags"] = tags

        try:
            start = self.http.post(
                UPLOAD_URL,
                params={"uploadType": "resumable", "part": "snippet,status"},
                json=metadata,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Upload-Content-Length": str(media.size),
                    "X-Upload-Content-Type": media.mime_type,
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            raise PublishError(f"Network error talking to YouTube: {exc.__class__.__name__}") from exc
        self._check(start)
        upload_url = start.headers.get("Location")
        if not upload_url:
            raise PublishError("YouTube did not return an upload address.")

        try:
            with media.path.open("rb") as fh:
                done = self.http.put(
                    upload_url,
                    data=fh,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": media.mime_type,
                        "Content-Length": str(media.size),
                    },
                    timeout=3600,
                )
        except requests.RequestException as exc:
            raise PublishError(f"YouTube upload failed: {exc.__class__.__name__}") from exc
        video = self._check(done)
        video_id = video.get("id")
        if not video_id:
            raise PublishError("YouTube accepted the upload but returned no video id.")
        note = ""
        actual = (video.get("status") or {}).get("privacyStatus", self.privacy)
        if actual != self.privacy:
            note = f" (YouTube set it to {actual})"
        return Result.success(f"https://youtu.be/{video_id}{note}")
