"""Facebook Page and Instagram publishing through Meta's Graph API."""

from __future__ import annotations

import time
from typing import Any, Optional

import requests

from autopost.models import Post, PublishError, Result

GRAPH = "https://graph.facebook.com"
GRAPH_VIDEO = "https://graph-video.facebook.com"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"


class MetaClient:
    def __init__(self, page_token: str, version: str = "v23.0", session: Optional[requests.Session] = None,
                 poll_seconds: float = 10, max_wait_seconds: float = 900):
        self.token = page_token
        self.version = version
        self.http = session or requests.Session()
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds
        self._page: Optional[dict] = None

    # ---- low level -------------------------------------------------------------------------
    def request(self, method: str, path: str, host: str = GRAPH, **kwargs) -> dict:
        url = path if path.startswith("http") else f"{host}/{self.version}/{path.lstrip('/')}"
        headers = kwargs.pop("headers", {})
        headers.setdefault("Authorization", f"Bearer {self.token}")
        try:
            resp = self.http.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 300), **kwargs)
        except requests.RequestException as exc:
            raise PublishError(f"Network error talking to Meta: {exc.__class__.__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or (isinstance(body, dict) and "error" in body):
            err = body.get("error", {}) if isinstance(body, dict) else {}
            message = err.get("error_user_msg") or err.get("message") or resp.text[:300]
            code = err.get("code")
            hint = ""
            if code == 190:
                hint = " (The Meta token is invalid or expired; create a new META_PAGE_TOKEN.)"
            elif code in (10, 200) or (isinstance(code, int) and 200 <= code < 300):
                hint = " (A permission is missing; regenerate the token with all permissions in the guide.)"
            raise PublishError(f"Meta error {code}: {message}{hint}")
        return body

    # ---- account info ----------------------------------------------------------------------
    def page(self) -> dict:
        if self._page is None:
            self._page = self.request(
                "GET", "me", params={"fields": "id,name,instagram_business_account{id,username}"}
            )
        return self._page

    @property
    def page_id(self) -> str:
        return self.page()["id"]

    @property
    def instagram_id(self) -> Optional[str]:
        ig = self.page().get("instagram_business_account") or {}
        return ig.get("id")

    def permalink(self, object_id: str, fallback: str) -> str:
        try:
            data = self.request("GET", object_id, params={"fields": "permalink_url"})
            link = data.get("permalink_url") or ""
            if link.startswith("/"):
                link = "https://www.facebook.com" + link
            return link or fallback
        except PublishError:
            return fallback

    # ---- Facebook --------------------------------------------------------------------------
    def publish_facebook(self, post: Post) -> Result:
        message = post.caption_for("facebook")
        media = post.media
        if media is None:
            if not message and not post.link:
                return Result.skipped("Nothing to post (no caption, link or media)")
            data: dict[str, Any] = {"message": message}
            if post.link:
                data["link"] = post.link
            body = self.request("POST", f"{self.page_id}/feed", data=data)
            post_id = body["id"]
            return Result.success(self.permalink(post_id, f"https://www.facebook.com/{post_id}"))

        if media.is_image:
            with media.path.open("rb") as fh:
                body = self.request(
                    "POST",
                    f"{self.page_id}/photos",
                    data={"message": message, "published": "true"},
                    files={"source": (media.name, fh, media.mime_type)},
                )
            post_id = body.get("post_id") or body["id"]
            return Result.success(self.permalink(post_id, f"https://www.facebook.com/{post_id}"))

        if media.is_video:
            with media.path.open("rb") as fh:
                body = self.request(
                    "POST",
                    f"{self.page_id}/videos",
                    host=GRAPH_VIDEO,
                    data={"description": message},
                    files={"source": (media.name, fh, media.mime_type)},
                    timeout=1800,
                )
            video_id = body["id"]
            return Result.success(self.permalink(video_id, f"https://www.facebook.com/{video_id}"))

        raise PublishError(f"Unsupported media type for Facebook: {media.mime_type}")

    # ---- Instagram -------------------------------------------------------------------------
    def publish_instagram(self, post: Post, jpeg_path=None) -> Result:
        ig_id = self.instagram_id
        if not ig_id:
            raise PublishError(
                "No Instagram Business/Creator account is linked to this Facebook Page."
            )
        media = post.media
        if media is None:
            return Result.skipped("Instagram needs a photo or video")
        caption = post.caption_for("instagram")

        temp_photo_id = None
        try:
            if media.is_image:
                image_url, temp_photo_id = self._host_image(jpeg_path or media.path)
                container = self.request(
                    "POST", f"{ig_id}/media", data={"image_url": image_url, "caption": caption}
                )
                container_id = container["id"]
            elif media.is_video:
                container = self.request(
                    "POST",
                    f"{ig_id}/media",
                    data={
                        "media_type": "REELS",
                        "upload_type": "resumable",
                        "caption": caption,
                        "share_to_feed": "true",
                    },
                )
                container_id = container["id"]
                upload_url = container.get("uri") or f"{RUPLOAD}/{self.version}/{container_id}"
                with media.path.open("rb") as fh:
                    self.request(
                        "POST",
                        upload_url,
                        headers={
                            "Authorization": f"OAuth {self.token}",
                            "offset": "0",
                            "file_size": str(media.size),
                        },
                        data=fh,
                        timeout=1800,
                    )
            else:
                raise PublishError(f"Unsupported media type for Instagram: {media.mime_type}")

            self._wait_for_container(container_id)
            published = self.request("POST", f"{ig_id}/media_publish", data={"creation_id": container_id})
            media_id = published["id"]
            try:
                link = self.request("GET", media_id, params={"fields": "permalink"}).get("permalink", "")
            except PublishError:
                link = ""
            return Result.success(link or f"Published (media id {media_id})")
        finally:
            if temp_photo_id:
                try:
                    self.request("DELETE", temp_photo_id)
                except PublishError:
                    pass

    def _host_image(self, path) -> tuple[str, str]:
        """Instagram fetches photos from a public URL. Upload the photo to the Page as an
        *unpublished* (invisible) photo and hand Instagram that image's URL."""
        with open(path, "rb") as fh:
            body = self.request(
                "POST",
                f"{self.page_id}/photos",
                data={"published": "false"},
                files={"source": ("photo.jpg", fh, "image/jpeg")},
            )
        photo_id = body["id"]
        info = self.request("GET", photo_id, params={"fields": "images"})
        images = info.get("images") or []
        if not images:
            raise PublishError("Could not get a hosted URL for the photo from Facebook.")
        best = max(images, key=lambda im: im.get("width", 0) * im.get("height", 0))
        return best["source"], photo_id

    def _wait_for_container(self, container_id: str) -> None:
        deadline = time.monotonic() + self.max_wait_seconds
        while True:
            info = self.request("GET", container_id, params={"fields": "status_code,status"})
            code = info.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram could not process the media: {info.get('status') or code}")
            if time.monotonic() >= deadline:
                raise PublishError("Instagram is still processing the media after 15 minutes; will retry.")
            time.sleep(self.poll_seconds)
