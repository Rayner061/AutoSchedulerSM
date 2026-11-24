import json

import pytest
import responses
from responses import matchers

from autopost import tiktok
from autopost.meta import GRAPH, GRAPH_VIDEO, MetaClient
from autopost.models import Media, Post, PublishError
from autopost.tiktok import API, TikTokClient, chunk_plan

V = "v23.0"
G = f"{GRAPH}/{V}"
PAGE = {"id": "111", "name": "My Page", "instagram_business_account": {"id": "999", "username": "me"}}


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 2048)
    return Media(p, "video/mp4", 2048, "clip.mp4")


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "pic.jpg"
    p.write_bytes(b"\xff\xd8fakejpeg")
    return Media(p, "image/jpeg", p.stat().st_size, "pic.jpg")


def meta():
    return MetaClient("PAGE_TOKEN", V, poll_seconds=0, max_wait_seconds=5)


def add_page(rsps):
    rsps.get(f"{G}/me", json=PAGE, match=[matchers.header_matcher({"Authorization": "Bearer PAGE_TOKEN"})])


# ---- Facebook ------------------------------------------------------------------------------
@responses.activate
def test_facebook_text_and_link():
    add_page(responses)
    responses.post(f"{G}/111/feed", json={"id": "111_222"},
                   match=[matchers.urlencoded_params_matcher({"message": "Hello", "link": "https://x.com"})])
    responses.get(f"{G}/111_222", json={"permalink_url": "https://www.facebook.com/p/222"})
    res = meta().publish_facebook(Post(2, "Hello", link="https://x.com"))
    assert res.cell() == "✅ https://www.facebook.com/p/222"


@responses.activate
def test_facebook_video_uses_video_host(video):
    add_page(responses)
    responses.post(f"{GRAPH_VIDEO}/{V}/111/videos", json={"id": "555"})
    responses.get(f"{G}/555", json={"permalink_url": "/mypage/videos/555/"})
    post = Post(2, "Watch", media=video)
    res = meta().publish_facebook(post)
    assert res.message == "https://www.facebook.com/mypage/videos/555/"
    assert b'name="source"' in responses.calls[1].request.body  # file sent as multipart upload


@responses.activate
def test_meta_error_message_is_readable():
    responses.get(f"{G}/me", status=400, json={"error": {"message": "Invalid OAuth access token.", "code": 190}})
    with pytest.raises(PublishError, match="invalid or expired"):
        meta().publish_facebook(Post(2, "x"))


# ---- Instagram -----------------------------------------------------------------------------
@responses.activate
def test_instagram_reel_resumable_upload(video):
    add_page(responses)
    responses.post(f"{G}/999/media", json={"id": "C1", "uri": f"https://rupload.facebook.com/ig-api-upload/{V}/C1"},
                   match=[matchers.urlencoded_params_matcher(
                       {"media_type": "REELS", "upload_type": "resumable", "caption": "Reel!", "share_to_feed": "true"})])
    responses.post(f"https://rupload.facebook.com/ig-api-upload/{V}/C1", json={"success": True},
                   match=[matchers.header_matcher({"Authorization": "OAuth PAGE_TOKEN", "offset": "0", "file_size": "2048"})])
    responses.get(f"{G}/C1", json={"status_code": "IN_PROGRESS"})
    responses.get(f"{G}/C1", json={"status_code": "FINISHED"})
    responses.post(f"{G}/999/media_publish", json={"id": "M1"},
                   match=[matchers.urlencoded_params_matcher({"creation_id": "C1"})])
    responses.get(f"{G}/M1", json={"permalink": "https://www.instagram.com/reel/abc/"})
    res = meta().publish_instagram(Post(2, "Reel!", media=video))
    assert res.cell() == "✅ https://www.instagram.com/reel/abc/"


@responses.activate
def test_instagram_photo_hosted_via_unpublished_page_photo(image):
    add_page(responses)
    responses.post(f"{G}/111/photos", json={"id": "P1"})
    responses.get(f"{G}/P1", json={"images": [{"source": "https://cdn/small.jpg", "width": 100, "height": 100},
                                               {"source": "https://cdn/big.jpg", "width": 1080, "height": 1080}]})
    responses.post(f"{G}/999/media", json={"id": "C2"},
                   match=[matchers.urlencoded_params_matcher({"image_url": "https://cdn/big.jpg", "caption": "Pic"})])
    responses.get(f"{G}/C2", json={"status_code": "FINISHED"})
    responses.post(f"{G}/999/media_publish", json={"id": "M2"})
    responses.get(f"{G}/M2", json={"permalink": "https://www.instagram.com/p/xyz/"})
    responses.delete(f"{G}/P1", json={"success": True})
    res = meta().publish_instagram(Post(2, "Pic", media=image))
    assert res.message == "https://www.instagram.com/p/xyz/"
    assert any(c.request.method == "DELETE" for c in responses.calls)  # temp photo cleaned up


@responses.activate
def test_instagram_processing_error(video):
    add_page(responses)
    responses.post(f"{G}/999/media", json={"id": "C3"})
    responses.post(f"https://rupload.facebook.com/ig-api-upload/{V}/C3", json={"success": True})
    responses.get(f"{G}/C3", json={"status_code": "ERROR", "status": "Error: video too short"})
    with pytest.raises(PublishError, match="too short"):
        meta().publish_instagram(Post(2, "x", media=video))


def test_instagram_text_only_is_skipped():
    client = meta()
    client._page = PAGE
    assert client.publish_instagram(Post(2, "just text")).cell().startswith("➖")


# ---- TikTok --------------------------------------------------------------------------------
def test_chunk_plan_rules():
    mb = 1024 * 1024
    assert chunk_plan(3 * mb) == (3 * mb, 1)
    assert chunk_plan(64 * mb) == (64 * mb, 1)
    size, count = chunk_plan(100 * mb)
    assert (size, count) == (32 * mb, 3)
    assert 100 * mb - (count - 1) * size <= 128 * mb  # last chunk within TikTok's limit
    with pytest.raises(PublishError):
        chunk_plan(0)


def tt_ok(data):
    return {"data": data, "error": {"code": "ok", "message": "", "log_id": "x"}}


@responses.activate
def test_tiktok_inbox_upload(video):
    responses.post(f"{API}/v2/post/publish/inbox/video/init/",
                   json=tt_ok({"publish_id": "PUB1", "upload_url": "https://upload.tiktok/u1"}),
                   match=[matchers.json_params_matcher({"source_info": {
                       "source": "FILE_UPLOAD", "video_size": 2048, "chunk_size": 2048, "total_chunk_count": 1}})])
    responses.put("https://upload.tiktok/u1", status=201,
                  match=[matchers.header_matcher({"Content-Range": "bytes 0-2047/2048", "Content-Type": "video/mp4"})])
    responses.post(f"{API}/v2/post/publish/status/fetch/", json=tt_ok({"status": "PROCESSING_UPLOAD"}))
    responses.post(f"{API}/v2/post/publish/status/fetch/", json=tt_ok({"status": "SEND_TO_USER_INBOX"}))
    res = TikTokClient("AT", mode="inbox", poll_seconds=0).publish(Post(2, "cap", media=video))
    assert res.done and res.cell().startswith("📥")


@responses.activate
def test_tiktok_direct_post_checks_privacy(video):
    responses.post(f"{API}/v2/post/publish/creator_info/query/", json=tt_ok(
        {"creator_username": "me", "privacy_level_options": ["SELF_ONLY"], "comment_disabled": False}))
    client = TikTokClient("AT", mode="direct", privacy="PUBLIC_TO_EVERYONE", poll_seconds=0)
    with pytest.raises(PublishError, match="Allowed: SELF_ONLY"):
        client.publish(Post(2, "cap", media=video))


@responses.activate
def test_tiktok_direct_post_success(video):
    responses.post(f"{API}/v2/post/publish/creator_info/query/", json=tt_ok(
        {"creator_username": "me", "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
         "duet_disabled": True}))
    responses.post(f"{API}/v2/post/publish/video/init/",
                   json=tt_ok({"publish_id": "PUB2", "upload_url": "https://upload.tiktok/u2"}))
    responses.put("https://upload.tiktok/u2", status=201)
    responses.post(f"{API}/v2/post/publish/status/fetch/",
                   json=tt_ok({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300]}))
    res = TikTokClient("AT", mode="direct", privacy="PUBLIC_TO_EVERYONE", poll_seconds=0).publish(
        Post(2, "cap", captions={"tiktok": "tt caption"}, media=video))
    assert res.message == "https://www.tiktok.com/@me/video/7300"
    init_body = json.loads(responses.calls[1].request.body)
    assert init_body["post_info"]["title"] == "tt caption"
    assert init_body["post_info"]["disable_duet"] is True


@responses.activate
def test_tiktok_error_hint(video):
    responses.post(f"{API}/v2/post/publish/inbox/video/init/", status=403, json={
        "data": {}, "error": {"code": "spam_risk_too_many_posts", "message": "limit"}})
    with pytest.raises(PublishError, match="daily posting limit"):
        TikTokClient("AT").publish(Post(2, "c", media=video))


@responses.activate
def test_tiktok_refresh_token():
    responses.post(f"{API}/v2/oauth/token/", json={"access_token": "AT", "refresh_token": "RT2", "expires_in": 86400},
                   match=[matchers.urlencoded_params_matcher({"client_key": "k", "client_secret": "s",
                                                              "grant_type": "refresh_token", "refresh_token": "RT1"})])
    assert tiktok.refresh("k", "s", "RT1")["refresh_token"] == "RT2"


@responses.activate
def test_tiktok_refresh_failure_is_clear():
    responses.post(f"{API}/v2/oauth/token/", json={"error": "invalid_grant", "error_description": "Refresh token is invalid"})
    with pytest.raises(PublishError, match="reconnect TikTok"):
        tiktok.refresh("k", "s", "bad")


def test_tiktok_photo_is_skipped(image):
    assert TikTokClient("AT").publish(Post(2, "c", media=image)).cell().startswith("➖")
