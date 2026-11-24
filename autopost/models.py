"""Shared data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PLATFORMS = ("facebook", "instagram", "tiktok", "youtube")

# The first character of a result cell tells the runner whether that platform is finished.
SUCCESS = "✅"
INBOX = "📥"
PROCESSING = "⏳"
SKIPPED = "➖"
FAILED = "❌"
DONE_MARKERS = (SUCCESS, INBOX, PROCESSING, SKIPPED)


class PublishError(Exception):
    """A platform rejected the post. The message is shown in the sheet."""


@dataclass
class Media:
    path: Path
    mime_type: str
    size: int
    name: str

    @property
    def is_video(self) -> bool:
        return self.mime_type.startswith("video/")

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")


@dataclass
class Post:
    row_number: int
    caption: str
    link: str = ""
    title: str = ""  # YouTube title
    media_ref: str = ""
    captions: dict = field(default_factory=dict)  # per-platform overrides
    media: Optional[Media] = None  # filled in after download

    def caption_for(self, platform: str) -> str:
        return self.captions.get(platform) or self.caption


@dataclass
class Result:
    marker: str
    message: str

    @property
    def done(self) -> bool:
        return self.marker in DONE_MARKERS

    def cell(self) -> str:
        return f"{self.marker} {self.message}".strip()

    @classmethod
    def success(cls, message: str) -> "Result":
        return cls(SUCCESS, message)

    @classmethod
    def skipped(cls, message: str) -> "Result":
        return cls(SKIPPED, message)

    @classmethod
    def failed(cls, message: str) -> "Result":
        return cls(FAILED, message)


def cell_is_done(cell: str) -> bool:
    return bool(cell) and cell.strip().startswith(DONE_MARKERS)
