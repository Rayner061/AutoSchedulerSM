from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from autopost.media import drive_file_id
from autopost.models import Media, PublishError, Result
from autopost.runner import Runner, parse_platforms
from autopost.sheet import Row, column_letter, normalize_header, parse_post_at

TZ = ZoneInfo("America/New_York")
NOW = datetime(2026, 12, 1, 20, 0, tzinfo=timezone.utc)  # 15:00 in New York


class FakeQueue:
    def __init__(self, rows):
        self._rows = [Row(i + 2, dict(r)) for i, r in enumerate(rows)]
        self.writes = []

    def rows(self):
        return iter(self._rows)

    def write(self, row, updates):
        self.writes.append((row.number, dict(updates)))
        row.values.update(updates)


def make_runner(rows, publishers, downloader=None, **kw):
    queue = FakeQueue(rows)
    dl = downloader or (lambda ref: Media(Path("/tmp/v.mp4"), "video/mp4", 10, "v.mp4"))
    return queue, Runner(queue, publishers, dl, TZ, now=NOW, echo=lambda m: None, **kw)


def ok(msg):
    return lambda: (lambda post: Result.success(msg))


def boom(msg):
    def publish(post):
        raise PublishError(msg)

    return lambda: publish


# ---- parsing -----------------------------------------------------------------------------
def test_parse_platforms_aliases_and_all():
    assert parse_platforms("FB, ig") == ["facebook", "instagram"]
    assert parse_platforms("Tik Tok and Instagram") == ["tiktok", "instagram"]
    assert parse_platforms("all") == ["facebook", "instagram", "tiktok"]
    with pytest.raises(ValueError):
        parse_platforms("myspace")
    with pytest.raises(ValueError):
        parse_platforms("")


def test_parse_post_at_serial_and_text():
    # 46357.625 = 2026-12-01 15:00 local
    assert parse_post_at(46357.625, TZ) == NOW
    assert parse_post_at("2026-12-01 15:00", TZ) == NOW
    assert parse_post_at("2026-12-01T20:00:00Z", TZ) == NOW
    assert parse_post_at("", TZ) is None
    assert parse_post_at("not a date", TZ) is None


def test_headers_and_columns():
    assert normalize_header("Post at") == "post_at"
    assert normalize_header("TikTok result") == "tiktok_result"
    assert normalize_header("Caption (Instagram)") == "caption_instagram"
    assert normalize_header("Date") == "post_at"
    assert column_letter(1) == "A" and column_letter(26) == "Z" and column_letter(28) == "AB"


def test_drive_ids():
    fid = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert drive_file_id(f"https://drive.google.com/file/d/{fid}/view?usp=sharing") == fid
    assert drive_file_id(f"https://drive.google.com/open?id={fid}") == fid
    assert drive_file_id(fid) == fid
    assert drive_file_id("https://example.com/video.mp4") is None


# ---- runner ------------------------------------------------------------------------------
def test_due_row_posts_everywhere():
    q, r = make_runner(
        [{"post_at": "2026-12-01 14:50", "platforms": "all", "caption": "hi", "media": "x", "status": ""}],
        {"facebook": ok("fb"), "instagram": ok("ig"), "tiktok": ok("tt")},
    )
    s = r.run()
    row = q._rows[0].values
    assert s.posted == 1
    assert row["status"] == "posted"
    assert row["facebook_result"] == "✅ fb" and row["tiktok_result"] == "✅ tt"
    assert q.writes[0][1]["status"] == "processing"  # marked before publishing


def test_future_and_draft_rows_are_left_alone():
    q, r = make_runner(
        [
            {"post_at": "2026-12-01 16:00", "platforms": "fb", "caption": "later"},
            {"post_at": "2026-12-01 10:00", "platforms": "fb", "caption": "draft", "status": "Draft"},
            {"post_at": "2026-12-01 10:00", "platforms": "fb", "caption": "done", "status": "posted"},
        ],
        {"facebook": ok("fb")},
    )
    s = r.run()
    assert q.writes == [] and s.posted == 0 and s.skipped_not_due == 1


def test_partial_failure_retries_only_failed_platform():
    calls = []

    def track(name, fail):
        def factory():
            def publish(post):
                calls.append(name)
                if fail():
                    raise PublishError("nope")
                return Result.success(name)

            return publish

        return factory

    fail_ig = {"v": True}
    rows = [{"post_at": "2026-12-01 14:00", "platforms": "fb, ig", "caption": "c", "media": "x"}]
    q, r = make_runner(rows, {"facebook": track("fb", lambda: False), "instagram": track("ig", lambda: fail_ig["v"])})
    s = r.run()
    row = q._rows[0]
    assert s.retrying == 1 and row.values["status"] == "retrying" and row.values["attempts"] == 1
    assert row.values["instagram_result"].startswith("❌")

    # next run: Instagram works now; Facebook must NOT be posted again
    fail_ig["v"] = False
    r2 = Runner(q, r.publisher_factories, r.downloader, TZ, now=NOW, echo=lambda m: None)
    r2.run()
    assert calls == ["fb", "ig", "ig"]
    assert row.values["status"] == "posted"


def test_gives_up_after_max_attempts():
    rows = [{"post_at": "2026-12-01 14:00", "platforms": "fb", "caption": "c", "attempts": "2", "status": "retrying"}]
    q, r = make_runner(rows, {"facebook": boom("bad")}, max_attempts=3)
    s = r.run()
    assert s.failed == 1 and s.needs_attention
    assert q._rows[0].values["status"].startswith("failed")


def test_too_late_marked_missed():
    q, r = make_runner([{"post_at": "2026-11-28 09:00", "platforms": "fb", "caption": "old"}], {"facebook": ok("fb")})
    r.run()
    assert q._rows[0].values["status"].startswith("missed")


def test_unconfigured_platform_and_bad_inputs():
    q, r = make_runner(
        [
            {"post_at": "2026-12-01 14:00", "platforms": "tiktok", "caption": "c", "media": "x"},
            {"post_at": "garbage", "platforms": "fb", "caption": "c"},
            {"post_at": "2026-12-01 14:00", "platforms": "snapchat", "caption": "c"},
        ],
        {"facebook": ok("fb")},
        max_attempts=1,
    )
    s = r.run()
    rows = [x.values for x in q._rows]
    assert "not set up" in rows[0]["tiktok_result"]
    assert rows[1]["status"].startswith("error")
    assert rows[2]["status"].startswith("error")
    assert s.errors == 2 and s.failed == 1


def test_media_download_failure_fails_each_platform():
    def bad_download(ref):
        raise PublishError("Drive file not found")

    q, r = make_runner(
        [{"post_at": "2026-12-01 14:00", "platforms": "fb,ig", "caption": "c", "media": "x"}],
        {"facebook": ok("fb"), "instagram": ok("ig")},
        downloader=bad_download,
    )
    r.run()
    v = q._rows[0].values
    assert v["facebook_result"] == "❌ Drive file not found" and v["status"] == "retrying"


def test_dry_run_writes_nothing():
    q, r = make_runner(
        [{"post_at": "2026-12-01 14:00", "platforms": "fb", "caption": "c"}], {"facebook": boom("x")}, dry_run=True
    )
    r.run()
    assert q.writes == []


def test_stuck_processing_row_is_not_reposted():
    q, r = make_runner(
        [{"post_at": "2026-12-01 14:00", "platforms": "fb", "caption": "c", "status": "processing"}],
        {"facebook": boom("should not be called")},
    )
    r.run()
    assert q.writes == []
