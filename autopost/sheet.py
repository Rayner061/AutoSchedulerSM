"""The Google Sheet that holds the posting queue, plus a small key/value state tab."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser as date_parser

from autopost.config import Config, ConfigError

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Columns the person fills in (display names used when the sheet is created).
INPUT_HEADERS = [
    "Post at",
    "Platforms",
    "Caption",
    "Media",
    "Link",
    "Caption (Facebook)",
    "Caption (Instagram)",
    "Caption (TikTok)",
    "Caption (YouTube)",
    "Title (YouTube)",
]
# Columns the robot fills in. Added automatically if missing.
OUTPUT_HEADERS = [
    "Status",
    "Facebook result",
    "Instagram result",
    "TikTok result",
    "YouTube result",
    "Attempts",
    "Updated at",
]
ALL_HEADERS = INPUT_HEADERS + OUTPUT_HEADERS

ALIASES = {
    "date": "post_at",
    "when": "post_at",
    "time": "post_at",
    "post_time": "post_at",
    "publish_at": "post_at",
    "schedule": "post_at",
    "scheduled_at": "post_at",
    "scheduled_time": "post_at",
    "date_time": "post_at",
    "platform": "platforms",
    "networks": "platforms",
    "text": "caption",
    "message": "caption",
    "video": "media",
    "image": "media",
    "photo": "media",
    "file": "media",
    "media_url": "media",
    "url": "link",
    "title": "title_youtube",
    "youtube_title": "title_youtube",
}

STATE_TAB = "_state"
SHEETS_EPOCH = datetime(1899, 12, 30)  # day zero for Google Sheets date serial numbers


def normalize_header(header: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(header).strip().lower()).strip("_")
    return ALIASES.get(key, key)


def parse_post_at(value: Any, tz: ZoneInfo) -> Optional[datetime]:
    """Turn a sheet cell into an aware UTC datetime. Returns None if empty or unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        local = SHEETS_EPOCH + timedelta(days=float(value))
        return local.replace(tzinfo=tz).astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = date_parser.parse(text)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def column_letter(index: int) -> str:
    """1 -> A, 27 -> AA."""
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class Row:
    def __init__(self, number: int, values: dict[str, Any]):
        self.number = number
        self.values = values

    def raw(self, key: str) -> Any:
        return self.values.get(key, "")

    def text(self, key: str) -> str:
        value = self.values.get(key, "")
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value).strip()

    def is_blank(self) -> bool:
        return not any(self.text(k) for k in ("post_at", "platforms", "caption", "media", "link"))


class SheetQueue:
    """Reads rows from the posts tab and writes results back, one row at a time."""

    def __init__(self, worksheet):
        self.ws = worksheet
        self.columns: dict[str, int] = {}
        self._rows: list[Row] = []

    def load(self) -> None:
        from gspread.utils import DateTimeOption, ValueRenderOption

        data = self.ws.get(
            value_render_option=ValueRenderOption.unformatted,
            date_time_render_option=DateTimeOption.serial_number,
            pad_values=True,
        )
        data = list(data or [])
        if not data or not any(str(c).strip() for c in data[0]):
            raise ConfigError(
                f"The '{self.ws.title}' tab has no header row. Run the 'init-sheet' command "
                "from GitHub Actions to create it."
            )
        header = data[0]
        self.columns = {}
        for idx, name in enumerate(header, start=1):
            key = normalize_header(name)
            if key and key not in self.columns:
                self.columns[key] = idx
        for required in ("post_at", "platforms"):
            if required not in self.columns:
                raise ConfigError(
                    f"The '{self.ws.title}' tab needs a '{required.replace('_', ' ').title()}' column."
                )
        self._ensure_output_columns(len(header))
        self._rows = []
        for offset, raw in enumerate(data[1:], start=2):
            values = {
                key: (raw[col - 1] if col - 1 < len(raw) else "") for key, col in self.columns.items()
            }
            self._rows.append(Row(offset, values))

    def _ensure_output_columns(self, header_len: int) -> None:
        missing = [h for h in OUTPUT_HEADERS if normalize_header(h) not in self.columns]
        if not missing:
            return
        start = header_len + 1
        needed = start + len(missing) - 1
        if needed > self.ws.col_count:
            self.ws.add_cols(needed - self.ws.col_count)
        rng = f"{column_letter(start)}1:{column_letter(needed)}1"
        self.ws.update(values=[missing], range_name=rng, raw=True)
        for i, name in enumerate(missing):
            self.columns[normalize_header(name)] = start + i

    def rows(self) -> Iterator[Row]:
        return iter(self._rows)

    def write(self, row: Row, updates: dict[str, Any]) -> None:
        batch = []
        for key, value in updates.items():
            col = self.columns.get(key)
            if col is None:
                continue
            row.values[key] = value
            batch.append({"range": f"{column_letter(col)}{row.number}", "values": [[value]]})
        if batch:
            self.ws.batch_update(batch, raw=True)


class StateStore:
    """Key/value storage in a '_state' tab (used for the rotating TikTok refresh token)."""

    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self._ws = None

    def _sheet(self):
        if self._ws is None:
            import gspread

            try:
                self._ws = self.spreadsheet.worksheet(STATE_TAB)
            except gspread.WorksheetNotFound:
                self._ws = self.spreadsheet.add_worksheet(STATE_TAB, rows=20, cols=2)
                self._ws.update(values=[["key", "value"]], range_name="A1:B1", raw=True)
        return self._ws

    def get(self, key: str) -> str:
        for row in self._sheet().get_all_values():
            if row and row[0] == key:
                return row[1] if len(row) > 1 else ""
        return ""

    def set(self, key: str, value: str) -> None:
        ws = self._sheet()
        rows = ws.get_all_values()
        for idx, row in enumerate(rows, start=1):
            if row and row[0] == key:
                ws.update(values=[[value]], range_name=f"B{idx}", raw=True)
                return
        ws.append_row([key, value], value_input_option="RAW")


class GoogleWorkspace:
    """Authenticated access to the spreadsheet and to Google Drive downloads."""

    def __init__(self, config: Config):
        import gspread
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials

        self.config = config
        try:
            creds = Credentials.from_service_account_info(config.google_service_account, scopes=SCOPES)
        except (ValueError, KeyError) as exc:
            raise ConfigError(f"The Google service-account JSON looks incomplete: {exc}") from exc
        self.service_account_email = config.google_service_account.get("client_email", "?")
        self.client = gspread.authorize(creds)
        self.drive = AuthorizedSession(creds)
        try:
            self.spreadsheet = self.client.open_by_key(config.sheet_id)
            _ = self.spreadsheet.title
        except Exception as exc:  # gspread raises several types for "no access"
            raise ConfigError(
                "Could not open the Google Sheet. Check SHEET_ID and share the sheet (as Editor) "
                f"with {self.service_account_email}. Details: {exc}"
            ) from exc

    def timezone(self) -> ZoneInfo:
        name = self.config.timezone or getattr(self.spreadsheet, "timezone", "") or "UTC"
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown time zone {name!r}. Use a name like America/New_York.") from exc

    def posts_tab(self):
        import gspread

        try:
            return self.spreadsheet.worksheet(self.config.sheet_tab)
        except gspread.WorksheetNotFound as exc:
            raise ConfigError(
                f"No tab named '{self.config.sheet_tab}' in the sheet. Run the 'init-sheet' command "
                "or set SHEET_TAB to your tab's name."
            ) from exc

    def init_posts_tab(self) -> str:
        """Create the posts tab with headers and an example row. Safe to run more than once."""
        import gspread

        try:
            ws = self.spreadsheet.worksheet(self.config.sheet_tab)
            existing = ws.row_values(1)
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(self.config.sheet_tab, rows=500, cols=len(ALL_HEADERS))
            existing = []
        if any(c.strip() for c in existing):
            have = {normalize_header(h) for h in existing}
            missing = [h for h in ALL_HEADERS if normalize_header(h) not in have]
            if missing:
                start = len(existing) + 1
                end = start + len(missing) - 1
                if end > ws.col_count:
                    ws.add_cols(end - ws.col_count)
                ws.update(values=[missing], range_name=f"{column_letter(start)}1:{column_letter(end)}1", raw=True)
            return f"Tab '{ws.title}' already existed; added {len(missing)} missing column(s)."
        ws.update(
            values=[
                ALL_HEADERS,
                [
                    "2030-01-01 09:00",
                    "all",
                    "Example post - change the date, add a Drive link to a video, then clear the Status cell.",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "draft",
                ],
            ],
            range_name=f"A1:{column_letter(len(ALL_HEADERS))}2",
            value_input_option="USER_ENTERED",
        )
        try:
            ws.freeze(rows=1)
            ws.format(f"A1:{column_letter(len(ALL_HEADERS))}1", {"textFormat": {"bold": True}})
        except Exception:
            pass
        return f"Created tab '{ws.title}' with headers and an example row."
