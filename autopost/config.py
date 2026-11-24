"""Settings, read from environment variables (GitHub Secrets / Variables)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass


class ConfigError(Exception):
    """A required setting is missing or invalid."""


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def extract_sheet_id(value: str) -> str:
    """Accept either a full Google Sheets URL or a bare spreadsheet ID."""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", value)
    return match.group(1) if match else value.strip()


@dataclass
class Config:
    google_service_account: dict
    sheet_id: str
    sheet_tab: str
    timezone: str  # empty = use the spreadsheet's own time zone

    meta_page_token: str
    graph_version: str

    tiktok_client_key: str
    tiktok_client_secret: str
    tiktok_refresh_token: str  # optional bootstrap; normally kept in the _state tab
    tiktok_redirect_uri: str
    tiktok_mode: str  # "inbox" or "direct"
    tiktok_privacy: str

    max_attempts: int
    max_late_hours: int
    dry_run: bool

    @property
    def meta_enabled(self) -> bool:
        return bool(self.meta_page_token)

    @property
    def tiktok_app_configured(self) -> bool:
        return bool(self.tiktok_client_key and self.tiktok_client_secret)

    @classmethod
    def from_env(cls) -> "Config":
        raw_sa = _env("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not raw_sa:
            raise ConfigError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not set. Paste the whole service-account "
                "JSON key file into this GitHub secret."
            )
        try:
            service_account = json.loads(raw_sa)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the entire contents "
                "of the downloaded key file, including the { and } characters."
            ) from exc

        sheet = _env("SHEET_ID")
        if not sheet:
            raise ConfigError("SHEET_ID is not set. Use the Google Sheet's URL or ID.")

        mode = _env("TIKTOK_MODE", "inbox").lower()
        if mode not in {"inbox", "direct"}:
            raise ConfigError("TIKTOK_MODE must be 'inbox' or 'direct'.")

        privacy = _env("TIKTOK_PRIVACY", "SELF_ONLY").upper()
        allowed_privacy = {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}
        if privacy not in allowed_privacy:
            raise ConfigError(f"TIKTOK_PRIVACY must be one of {sorted(allowed_privacy)}.")

        version = _env("GRAPH_API_VERSION", "v23.0")
        if not version.startswith("v"):
            version = "v" + version

        return cls(
            google_service_account=service_account,
            sheet_id=extract_sheet_id(sheet),
            sheet_tab=_env("SHEET_TAB", "Posts"),
            timezone=_env("TIMEZONE"),
            meta_page_token=_env("META_PAGE_TOKEN"),
            graph_version=version,
            tiktok_client_key=_env("TIKTOK_CLIENT_KEY"),
            tiktok_client_secret=_env("TIKTOK_CLIENT_SECRET"),
            tiktok_refresh_token=_env("TIKTOK_REFRESH_TOKEN"),
            tiktok_redirect_uri=_env("TIKTOK_REDIRECT_URI"),
            tiktok_mode=mode,
            tiktok_privacy=privacy,
            max_attempts=max(1, _int("MAX_ATTEMPTS", 3)),
            max_late_hours=max(1, _int("MAX_LATE_HOURS", 24)),
            dry_run=_bool("DRY_RUN"),
        )


def mask_secret(value: str) -> None:
    """Hide a secret from GitHub Actions logs (no-op when running elsewhere)."""
    if value and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{value}", flush=True)
