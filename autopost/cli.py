"""Commands: run, dry-run, check, init-sheet, tiktok-login-url, tiktok-connect, youtube-connect."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from autopost import meta as meta_api
from autopost import tiktok as tiktok_api
from autopost import youtube as youtube_api
from autopost.config import Config, ConfigError, mask_secret
from autopost.media import download, to_jpeg
from autopost.models import PublishError
from autopost.runner import Runner
from autopost.sheet import GoogleWorkspace, SheetQueue, StateStore

TIKTOK_REFRESH_KEY = "tiktok_refresh_token"
YOUTUBE_REFRESH_KEY = "youtube_refresh_token"


def tiktok_refresh_token(cfg: Config, state: StateStore) -> str:
    return state.get(TIKTOK_REFRESH_KEY) or cfg.tiktok_refresh_token


def tiktok_client(cfg: Config, state: StateStore) -> tiktok_api.TikTokClient:
    if not cfg.tiktok_app_configured:
        raise PublishError("TikTok is not set up (TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET missing).")
    refresh_token = tiktok_refresh_token(cfg, state)
    if not refresh_token:
        raise PublishError("TikTok is not connected yet. Run the 'tiktok-connect' step in the guide.")
    tokens = tiktok_api.refresh(cfg.tiktok_client_key, cfg.tiktok_client_secret, refresh_token)
    mask_secret(tokens["access_token"])
    new_refresh = tokens.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        mask_secret(new_refresh)
        state.set(TIKTOK_REFRESH_KEY, new_refresh)
    return tiktok_api.TikTokClient(tokens["access_token"], mode=cfg.tiktok_mode, privacy=cfg.tiktok_privacy)


def youtube_client(cfg: Config, state: StateStore) -> youtube_api.YouTubeClient:
    if not cfg.youtube_app_configured:
        raise PublishError("YouTube is not set up (YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET missing).")
    refresh_token = state.get(YOUTUBE_REFRESH_KEY)
    if not refresh_token:
        raise PublishError("YouTube is not connected yet. Run the 'youtube-connect' step in the guide.")
    tokens = youtube_api.refresh(cfg.youtube_client_id, cfg.youtube_client_secret, refresh_token)
    mask_secret(tokens["access_token"])
    return youtube_api.YouTubeClient(
        tokens["access_token"],
        privacy=cfg.youtube_privacy,
        category_id=cfg.youtube_category_id,
        made_for_kids=cfg.youtube_made_for_kids,
    )


def build_publishers(cfg: Config, state: StateStore) -> dict:
    publishers = {}
    if cfg.meta_enabled:
        shared: dict = {}

        def meta_client():
            if "client" not in shared:
                shared["client"] = meta_api.MetaClient(cfg.meta_page_token, cfg.graph_version)
            return shared["client"]

        def facebook():
            return meta_client().publish_facebook

        def instagram():
            client = meta_client()

            def publish(post):
                jpeg = to_jpeg(post.media) if post.media and post.media.is_image else None
                return client.publish_instagram(post, jpeg_path=jpeg)

            return publish

        publishers["facebook"] = facebook
        publishers["instagram"] = instagram

    if cfg.tiktok_app_configured:
        publishers["tiktok"] = lambda: tiktok_client(cfg, state).publish
    if cfg.youtube_app_configured:
        publishers["youtube"] = lambda: youtube_client(cfg, state).publish
    return publishers


def cmd_run(cfg: Config, dry_run: bool) -> int:
    google = GoogleWorkspace(cfg)
    tz = google.timezone()
    queue = SheetQueue(google.posts_tab())
    queue.load()
    state = StateStore(google.spreadsheet)

    with tempfile.TemporaryDirectory(prefix="autopost-") as tmp:
        counter = {"n": 0}

        def downloader(ref: str):
            counter["n"] += 1
            return download(ref, Path(tmp) / str(counter["n"]), drive_session=google.drive)

        runner = Runner(
            queue,
            build_publishers(cfg, state),
            downloader,
            tz,
            max_attempts=cfg.max_attempts,
            max_late_hours=cfg.max_late_hours,
            dry_run=dry_run or cfg.dry_run,
        )
        summary = runner.run()

    print(
        f"Done. posted={summary.posted} retrying={summary.retrying} failed={summary.failed} "
        f"errors={summary.errors} not_due_yet={summary.skipped_not_due} (time zone {tz.key})"
    )
    if summary.needs_attention:
        print("Some posts need attention - check the Status column in your sheet.")
        return 1
    return 0


def cmd_check(cfg: Config) -> int:
    ok = True
    print("Checking your setup...\n")
    try:
        google = GoogleWorkspace(cfg)
        print(f"[OK] Google Sheet: '{google.spreadsheet.title}' (time zone {google.timezone().key})")
        print(f"     Service account: {google.service_account_email}")
        queue = SheetQueue(google.posts_tab())
        queue.load()
        print(f"[OK] Posts tab '{cfg.sheet_tab}' has {sum(1 for r in queue.rows() if not r.is_blank())} post row(s)")
        state = StateStore(google.spreadsheet)
    except ConfigError as exc:
        print(f"[!!] Google: {exc}")
        return 1

    if cfg.meta_enabled:
        try:
            client = meta_api.MetaClient(cfg.meta_page_token, cfg.graph_version)
            page = client.page()
            print(f"[OK] Facebook Page: {page.get('name')} (id {page.get('id')})")
            ig = page.get("instagram_business_account")
            if ig:
                print(f"[OK] Instagram: @{ig.get('username')} (id {ig.get('id')})")
            else:
                ok = False
                print("[!!] Instagram: no Business/Creator account linked to this Page")
        except PublishError as exc:
            ok = False
            print(f"[!!] Meta: {exc}")
    else:
        print("[--] Facebook/Instagram: not set up (META_PAGE_TOKEN missing)")

    if cfg.tiktok_app_configured:
        try:
            client = tiktok_client(cfg, state)
            user = client.user_info()
            name = user.get("display_name") or "connected"
            print(f"[OK] TikTok: {name} (mode: {cfg.tiktok_mode}"
                  f"{', privacy: ' + cfg.tiktok_privacy if cfg.tiktok_mode == 'direct' else ''})")
            if cfg.tiktok_mode == "direct":
                info = client.creator_info()
                print(f"     Allowed privacy levels: {', '.join(info.get('privacy_level_options', []))}")
                print(f"     Max video length: {info.get('max_video_post_duration_sec')} seconds")
        except PublishError as exc:
            ok = False
            print(f"[!!] TikTok: {exc}")
    else:
        print("[--] TikTok: not set up (TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET missing)")

    if cfg.youtube_app_configured:
        try:
            channel = youtube_client(cfg, state).channel()
            title = (channel.get("snippet") or {}).get("title")
            if title:
                print(f"[OK] YouTube: {title} (uploads as {cfg.youtube_privacy})")
            else:
                ok = False
                print("[!!] YouTube: connected, but this Google account has no YouTube channel")
        except PublishError as exc:
            ok = False
            print(f"[!!] YouTube: {exc}")
    else:
        print("[--] YouTube: not set up (YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET missing)")

    print("\nAll good!" if ok else "\nSomething needs fixing - see the lines marked [!!].")
    return 0 if ok else 1


def cmd_init_sheet(cfg: Config) -> int:
    google = GoogleWorkspace(cfg)
    print(google.init_posts_tab())
    StateStore(google.spreadsheet).get("_init")  # creates the _state tab
    print("Created/verified the '_state' tab (the robot keeps its TikTok login there - don't edit it).")
    return 0


def cmd_tiktok_login_url(cfg: Config) -> int:
    if not cfg.tiktok_client_key or not cfg.tiktok_redirect_uri:
        print("Set the TIKTOK_CLIENT_KEY secret and the TIKTOK_REDIRECT_URI variable first.")
        return 1
    print("Open this link, log in to TikTok and approve:\n")
    print(tiktok_api.login_url(cfg.tiktok_client_key, cfg.tiktok_redirect_uri))
    return 0


def clean_code(code: str) -> str:
    code = (code or "").strip()
    if "code=" in code:  # someone pasted the whole URL
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(code).query).get("code", [""])[0]
    return code


def cmd_youtube_connect(cfg: Config, code: str) -> int:
    code = clean_code(code)
    if not code:
        print("No code given. Paste the code from the YouTube callback page into the 'login_code' box.")
        return 1
    if not (cfg.youtube_app_configured and cfg.youtube_redirect_uri):
        print("Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET (secrets) and YOUTUBE_REDIRECT_URI (variable) first.")
        return 1
    google = GoogleWorkspace(cfg)
    tokens = youtube_api.exchange_code(
        cfg.youtube_client_id, cfg.youtube_client_secret, code, cfg.youtube_redirect_uri
    )
    mask_secret(tokens["access_token"])
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("Google did not return a long-term login. Remove the app's access at "
              "myaccount.google.com/permissions, then connect again.")
        return 1
    mask_secret(refresh_token)
    StateStore(google.spreadsheet).set(YOUTUBE_REFRESH_KEY, refresh_token)
    channel = youtube_api.YouTubeClient(tokens["access_token"]).channel()
    print(f"YouTube connected: {(channel.get('snippet') or {}).get('title', 'no channel found!')}")
    return 0


def cmd_tiktok_connect(cfg: Config, code: str) -> int:
    code = clean_code(code)
    if not code:
        print("No code given. Paste the code from the TikTok callback page into the 'login_code' box.")
        return 1
    if not (cfg.tiktok_app_configured and cfg.tiktok_redirect_uri):
        print("Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET (secrets) and TIKTOK_REDIRECT_URI (variable) first.")
        return 1
    google = GoogleWorkspace(cfg)
    tokens = tiktok_api.exchange_code(
        cfg.tiktok_client_key, cfg.tiktok_client_secret, code, cfg.tiktok_redirect_uri
    )
    mask_secret(tokens["access_token"])
    mask_secret(tokens.get("refresh_token", ""))
    StateStore(google.spreadsheet).set(TIKTOK_REFRESH_KEY, tokens["refresh_token"])
    granted = tokens.get("scope", "")
    print(f"TikTok connected. Permissions granted: {granted}")
    if "video.upload" not in granted and "video.publish" not in granted:
        print("Warning: no posting permission was granted. Check the Content Posting API in your TikTok app.")
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="autopost", description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "dry-run", "check", "init-sheet", "tiktok-login-url", "tiktok-connect", "youtube-connect"],
    )
    parser.add_argument("--code", default=os.environ.get("LOGIN_CODE", ""), help="Login code from a callback page")
    args = parser.parse_args(argv)

    try:
        cfg = Config.from_env()
        mask_secret(cfg.meta_page_token)
        mask_secret(cfg.tiktok_client_secret)
        mask_secret(cfg.youtube_client_secret)
        if args.command == "run":
            return cmd_run(cfg, dry_run=False)
        if args.command == "dry-run":
            return cmd_run(cfg, dry_run=True)
        if args.command == "check":
            return cmd_check(cfg)
        if args.command == "init-sheet":
            return cmd_init_sheet(cfg)
        if args.command == "tiktok-login-url":
            return cmd_tiktok_login_url(cfg)
        if args.command == "tiktok-connect":
            return cmd_tiktok_connect(cfg, args.code)
        if args.command == "youtube-connect":
            return cmd_youtube_connect(cfg, args.code)
    except (ConfigError, PublishError) as exc:
        print(f"Setup problem: {exc}", file=sys.stderr)
        return 2
    return 0
