"""Decides which rows are due and publishes them, writing results back to the sheet."""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from autopost.models import PLATFORMS, Media, Post, PublishError, Result, cell_is_done
from autopost.sheet import Row, parse_post_at

READY = {"", "ready", "scheduled", "queued", "pending", "retrying", "retry", "todo"}
ON_HOLD = {"draft", "paused", "hold", "skip", "wait"}
PROCESSING = "processing"

PLATFORM_ALIASES = {
    "fb": "facebook", "facebook": "facebook", "meta": "facebook",
    "ig": "instagram", "insta": "instagram", "instagram": "instagram",
    "tt": "tiktok", "tiktok": "tiktok", "tik_tok": "tiktok",
}

Publisher = Callable[[Post], Result]


def parse_platforms(text: str) -> list[str]:
    tokens = [t for t in re.split(r"[,;/|+&\s]+", text.strip().lower().replace("tik tok", "tiktok")) if t]
    if not tokens:
        raise ValueError("Platforms is empty. Use: facebook, instagram, tiktok (or 'all').")
    if any(t in {"all", "everything", "every"} for t in tokens):
        return list(PLATFORMS)
    found: list[str] = []
    for token in tokens:
        if token in {"and"}:
            continue
        name = PLATFORM_ALIASES.get(token)
        if not name:
            raise ValueError(f"Unknown platform '{token}'. Use facebook, instagram, tiktok or all.")
        if name not in found:
            found.append(name)
    return found


@dataclass
class Summary:
    posted: int = 0
    retrying: int = 0
    failed: int = 0
    errors: int = 0
    skipped_not_due: int = 0
    log: list[str] = field(default_factory=list)

    @property
    def needs_attention(self) -> bool:
        return bool(self.failed or self.errors)


class Runner:
    def __init__(self, queue, publishers: dict[str, Callable[[], Publisher]],
                 downloader: Callable[[str], Media], tz: ZoneInfo, max_attempts: int = 3,
                 max_late_hours: int = 24, dry_run: bool = False,
                 now: Optional[datetime] = None, echo: Callable[[str], None] = print):
        self.queue = queue
        self.publisher_factories = publishers
        self._publishers: dict[str, Publisher] = {}
        self.downloader = downloader
        self.tz = tz
        self.max_attempts = max_attempts
        self.max_late = timedelta(hours=max_late_hours)
        self.dry_run = dry_run
        self.now = now or datetime.now(timezone.utc)
        self.echo = echo
        self.summary = Summary()

    def say(self, message: str) -> None:
        self.summary.log.append(message)
        self.echo(message)

    def local_now(self) -> str:
        return self.now.astimezone(self.tz).strftime("%Y-%m-%d %H:%M")

    def publisher(self, platform: str) -> Publisher:
        if platform not in self._publishers:
            factory = self.publisher_factories.get(platform)
            if factory is None:
                raise PublishError(f"{platform.title()} is not set up yet (see the setup guide).")
            self._publishers[platform] = factory()
        return self._publishers[platform]

    def write(self, row: Row, updates: dict) -> None:
        if not self.dry_run:
            self.queue.write(row, updates)

    # ------------------------------------------------------------------------------------
    def run(self) -> Summary:
        for row in self.queue.rows():
            try:
                self.process(row)
            except Exception as exc:  # never let one bad row stop the others
                self.summary.errors += 1
                self.say(f"Row {row.number}: unexpected error {exc!r}")
                traceback.print_exc()
                self.write(row, {"status": "error", "updated_at": self.local_now()})
        return self.summary

    def process(self, row: Row) -> None:
        if row.is_blank():
            return
        status = row.text("status").lower()
        if status in ON_HOLD or (status not in READY and status != PROCESSING):
            return  # draft, posted, failed, missed, error, or anything custom
        if status == PROCESSING:
            self.say(
                f"Row {row.number}: still marked 'processing' from an earlier run that stopped early. "
                "Check the platforms, then set Status to 'ready' to retry the missing ones."
            )
            return

        post_at = parse_post_at(row.raw("post_at"), self.tz)
        if post_at is None:
            self.summary.errors += 1
            self.say(f"Row {row.number}: can't read the date in 'Post at'.")
            self.write(row, {"status": "error: invalid Post at date", "updated_at": self.local_now()})
            return
        if post_at > self.now:
            self.summary.skipped_not_due += 1
            return

        attempts = int(row.text("attempts") or 0) if row.text("attempts").isdigit() else 0
        if attempts == 0 and self.now - post_at > self.max_late:
            self.say(f"Row {row.number}: more than {self.max_late} late; marked as missed.")
            self.write(row, {"status": "missed (too late - change the date and clear Status to post)",
                             "updated_at": self.local_now()})
            return

        try:
            platforms = parse_platforms(row.text("platforms"))
        except ValueError as exc:
            self.summary.errors += 1
            self.say(f"Row {row.number}: {exc}")
            self.write(row, {"status": f"error: {exc}", "updated_at": self.local_now()})
            return

        todo = [p for p in platforms if not cell_is_done(row.text(f"{p}_result"))]
        post = Post(
            row_number=row.number,
            caption=row.text("caption"),
            link=row.text("link"),
            media_ref=row.text("media"),
            captions={p: row.text(f"caption_{p}") for p in PLATFORMS if row.text(f"caption_{p}")},
        )

        if self.dry_run:
            self.say(f"[dry run] Row {row.number} is due: would post to {', '.join(todo) or 'nothing'}"
                     f"{' with media ' + post.media_ref if post.media_ref else ''}.")
            return

        self.say(f"Row {row.number}: posting to {', '.join(todo)}")
        self.write(row, {"status": PROCESSING, "updated_at": self.local_now()})

        media_error: Optional[str] = None
        if post.media_ref and todo:
            try:
                post.media = self.downloader(post.media_ref)
            except PublishError as exc:
                media_error = str(exc)

        all_done = True
        for platform in platforms:
            column = f"{platform}_result"
            if platform not in todo:
                continue
            if media_error:
                result = Result.failed(media_error)
            else:
                try:
                    result = self.publisher(platform)(post)
                except PublishError as exc:
                    result = Result.failed(str(exc))
                except Exception as exc:
                    traceback.print_exc()
                    result = Result.failed(f"Unexpected error: {exc.__class__.__name__}: {exc}")
            self.say(f"  {platform}: {result.cell()}")
            # Write each result right away so a crash never causes a double post.
            self.write(row, {column: result.cell()[:49000]})
            if not result.done:
                all_done = False

        if all_done:
            self.summary.posted += 1
            self.write(row, {"status": "posted", "updated_at": self.local_now()})
            return

        attempts += 1
        if attempts >= self.max_attempts:
            self.summary.failed += 1
            final = "failed (fix the problem, then set Status to 'ready' to retry)"
            self.write(row, {"status": final, "attempts": attempts, "updated_at": self.local_now()})
        else:
            self.summary.retrying += 1
            self.write(row, {"status": "retrying", "attempts": attempts, "updated_at": self.local_now()})
