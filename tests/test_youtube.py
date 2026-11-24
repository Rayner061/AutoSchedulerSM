import json

import pytest
import responses
from responses import matchers

from autopost import youtube
from autopost.models import Media, Post, PublishError
from autopost.runner import parse_platforms
from autopost.sheet import normalize_header
from autopost.youtube import UPLOAD_URL, YouTubeClient, make_title


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 1000)
    return Media(p, "video/mp4", 1000, "clip.mp4")


def test_title_rules():
    assert make_title(Post(2, "First line <b>\nsecond")) == "First line b"
    assert make_title(Post(2, "ignored", title="My Title")) == "My Title"
    assert len(make_title(Post(2, "x" * 300))) == 100
    assert make_title(Post(2, "")) == "New video"
    assert make_title(Post(2, "general", captions={"youtube": "\n\nYT line"})) == "YT line"


def test_youtube_in_platforms_and_headers():
    assert parse_platforms("all")[-1] == "youtube"
    assert parse_platforms("yt, shorts") == ["youtube"]
    assert normalize_header("Title (YouTube)") == "title_youtube"
    assert normalize_header("YouTube result") == "youtube_result"


@responses.activate
def test_resumable_upload(video):
    responses.post(UPLOAD_URL, status=200, headers={"Location": "https://upload.youtube/session1"},
                   match=[matchers.query_param_matcher({"uploadType": "resumable", "part": "snippet,status"}),
                          matchers.header_matcher({"Authorization": "Bearer AT", "X-Upload-Content-Length": "1000",
                                                   "X-Upload-Content-Type": "video/mp4"})])
    responses.put("https://upload.youtube/session1", json={"id": "VID123", "status": {"privacyStatus": "public"}},
                  match=[matchers.header_matcher({"Content-Length": "1000"})])
    res = YouTubeClient("AT").publish(Post(2, "Big news #launch #new", title="Launch day", media=video))
    assert res.cell() == "✅ https://youtu.be/VID123"
    meta = json.loads(responses.calls[0].request.body)
    assert meta["snippet"]["title"] == "Launch day"
    assert meta["snippet"]["tags"] == ["launch", "new"]
    assert meta["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False}


@responses.activate
def test_locked_private_is_reported(video):
    responses.post(UPLOAD_URL, headers={"Location": "https://upload.youtube/s2"}, json={})
    responses.put("https://upload.youtube/s2", json={"id": "V2", "status": {"privacyStatus": "private"}})
    assert YouTubeClient("AT").publish(Post(2, "x", media=video)).message == "https://youtu.be/V2 (YouTube set it to private)"


@responses.activate
def test_quota_error_hint(video):
    responses.post(UPLOAD_URL, status=403, json={"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
    with pytest.raises(PublishError, match="daily upload quota"):
        YouTubeClient("AT").publish(Post(2, "x", media=video))


@responses.activate
def test_refresh_invalid_grant_hint():
    responses.post(youtube.TOKEN_URL, status=400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
    with pytest.raises(PublishError, match="In production"):
        youtube.refresh("id", "secret", "rt")


def test_photo_skipped(tmp_path):
    p = tmp_path / "a.jpg"; p.write_bytes(b"x")
    assert YouTubeClient("AT").publish(Post(2, "c", media=Media(p, "image/jpeg", 1, "a.jpg"))).cell().startswith("➖")
