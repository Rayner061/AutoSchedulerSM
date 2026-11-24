# Autopost: free scheduled posting to Facebook, Instagram, TikTok and YouTube

You write your posts in a **Google Sheet**. Every 15 minutes, **GitHub Actions** (free) checks the
sheet and publishes anything that's due, using the **official, free APIs** of each platform. It then
writes the result back into the sheet.

```
Google Sheet  ──►  GitHub Actions (every 15 min)  ──►  Facebook Page   ✅ fully automatic
(+ photos/videos                                   ──►  Instagram       ✅ fully automatic
 in Google Drive)                                  ──►  TikTok          📥 sent to your inbox (1 tap to post)
                                                                         ✅ fully automatic after TikTok approves your app
                                                   ──►  YouTube         ✅ automatic upload (public after Google's free audit)
```

**Cost: $0.** No server, no paid services.

---

## Contents

1. [Put the code on GitHub](#step-1--put-the-code-on-github)
2. [Google Sheet and Drive](#step-2--google-sheet-and-drive)
3. [Turn on the small website (GitHub Pages)](#step-3--turn-on-the-small-website-github-pages)
4. [Facebook and Instagram](#step-4--facebook-and-instagram)
5. [TikTok](#step-5--tiktok)
6. [YouTube](#step-6--youtube)
7. [Test it](#step-7--test-it)
7. [Everyday use](#everyday-use)
8. [Limits and troubleshooting](#limits-and-troubleshooting)
9. [All settings](#all-settings)

Plan for **about 1 to 2 hours** the first time. Most of that is clicking through the Meta and TikTok developer sites.

---

## Step 1 — Put the code on GitHub

1. Create a new repository on GitHub, for example `autopost`.
   * **Public is recommended.** On the free plan you get unlimited Actions minutes and free GitHub Pages.
     Your **tokens stay private**: they live in encrypted *Secrets*, never in the code.
     Your posts stay private too, because they live in your Google Sheet, not in the repository.
   * **Private** also works. Then change the schedule in `.github/workflows/autopost.yml` from every
     15 minutes to every 30 minutes (`"7,37 * * * *"`) to stay inside the 2,000 free minutes per
     month, and see the TikTok note in Step 5.
2. Upload all the files from this folder, including the hidden `.github` folder.
3. Open the **Actions** tab. If GitHub asks, click **"I understand my workflows, go ahead and enable them"**.

You'll add settings in **Settings → Secrets and variables → Actions**:
* **Secrets** tab: passwords and tokens (hidden forever after saving).
* **Variables** tab: normal settings.

---

## Step 2 — Google Sheet and Drive

### 2a. Create a Google "robot" account (service account)

1. Go to <https://console.cloud.google.com/> and create a project (top bar → **New project**, name it `autopost`).
2. Open **APIs & Services → Library**. Search for and **Enable** both:
   * **Google Sheets API**
   * **Google Drive API**
3. Open **IAM & Admin → Service Accounts → Create service account**. Name it `autopost`, click
   **Create and continue**, skip the optional steps, then click **Done**.
4. Click the new service account → **Keys** tab → **Add key → Create new key → JSON**. A `.json` file downloads.
5. Copy the service account's **email** (it looks like `autopost@your-project.iam.gserviceaccount.com`).

### 2b. Create the sheet and the media folder

1. Create a new Google Sheet, for example "Autopost".
2. Click **Share**, paste the service account email and give it **Editor** access.
3. In Google Drive, create a folder, for example "Autopost media". **Share** it with the same email as **Viewer**.
   Put your photos and videos in this folder.

### 2c. Save the GitHub secrets

| Secret name | Value |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Open the downloaded `.json` file in a text editor and paste **everything** in it |
| `SHEET_ID` | The full link to your Google Sheet |

Then delete the `.json` file from your computer, or keep it somewhere safe.

---

## Step 3 — Turn on the small website (GitHub Pages)

Meta, TikTok and Google require a **privacy policy** link. TikTok and YouTube also need a web page to send you back to after you log in.
Both are included in the `docs/` folder.

1. In your repo go to **Settings → Pages**.
2. Under **Build and deployment**, choose **Deploy from a branch**, branch **main**, folder **/docs**, then **Save**.
3. After a minute your site is live at `https://YOUR-GITHUB-NAME.github.io/REPO-NAME/`.

You'll use these links:
* Privacy policy: `https://YOUR-GITHUB-NAME.github.io/REPO-NAME/privacy.html`
* Terms: `https://YOUR-GITHUB-NAME.github.io/REPO-NAME/terms.html`
* TikTok callback: `https://YOUR-GITHUB-NAME.github.io/REPO-NAME/tiktok-callback.html`
* YouTube callback: `https://YOUR-GITHUB-NAME.github.io/REPO-NAME/youtube-callback.html`

> **"Upgrade or make this repository public to enable Pages"?** Your repo is private. Either make it
> public (**Settings → General → Danger Zone → Change visibility**), or create a second, public repo
> that holds only the files from the `docs` folder, turn on Pages there with folder **/ (root)**, and use its links instead.

---

## Step 4 — Facebook and Instagram

You need a **Facebook Page**, and an **Instagram Business or Creator account linked to that Page**.
To check the link, open the Page → Settings → Linked accounts → Instagram.

### 4a. Create the Meta app

1. Go to <https://developers.facebook.com/> → **My Apps → Create App**.
2. If asked for a use case, choose **Other**. If asked for an app type, choose **Business**.
   Give it a name, for example "Autopost".
3. In the app's left menu, open **App settings → Basic**:
   * **Privacy Policy URL**: your `privacy.html` link from Step 3.
   * **Terms of Service URL**: your `terms.html` link.
   * Pick a **Category**, then click **Save changes**.
4. Add the **Instagram** product if it's offered ("Instagram API" / "API setup with Facebook login").

### 4b. Get a Page token that never expires

1. Open the **Graph API Explorer**: <https://developers.facebook.com/tools/explorer/>
2. On the right, choose your app under **Meta App**. Under **User or Page**, choose **Get User Access Token**.
3. Under **Permissions**, add all of these:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`,
   `instagram_content_publish`, `business_management`
4. Click **Generate Access Token**. In the pop-up, select **your Page and your Instagram account** and allow everything.
5. Copy the token. Open the **Access Token Debugger**: <https://developers.facebook.com/tools/debug/accesstoken/>.
   Paste the token, click **Debug**, then click **Extend Access Token** at the bottom. Copy the new, long-lived token.
6. Back in the Graph API Explorer, paste the **long-lived** token into the *Access Token* box and run this query:

   ```
   me/accounts?fields=name,access_token,instagram_business_account
   ```

7. In the result, find your Page and copy its `access_token` value. That's the **Page token**.
   Paste it into the Access Token Debugger to confirm it shows **Expires: Never**.
8. Save it as a GitHub **secret**:

| Secret name | Value |
|---|---|
| `META_PAGE_TOKEN` | The Page token from step 7 |

### 4c. Switch the app to Live

At the top of your app's dashboard, switch **App Mode** from *Development* to **Live**.
Meta can hide posts made by apps in Development mode from other people.
Because only you use the app, you don't need Meta's App Review.

---

## Step 5 — TikTok

TikTok works in two modes:

| Mode | What happens | When |
|---|---|---|
| **`inbox`** (default) | The video uploads to your TikTok **inbox**. TikTok notifies you; open it, paste the caption, tap **Post** | Works right away |
| **`direct`** | Posts automatically with the caption, no tapping | Posts are **private** until TikTok **audits** (approves) your app |

### 5a. Create the TikTok app

1. Go to <https://developers.tiktok.com/>, log in, then open **Manage apps → Connect an app**.
2. Fill in the app details: icon, name, category and description.
   * **Terms of Service URL** and **Privacy Policy URL**: your links from Step 3.
   * **Platform**: **Web**. **Website URL**: your GitHub Pages site.
   * If TikTok asks you to **verify the URL**, download its verification file, put it in the `docs/` folder and commit.
3. **Add products**:
   * **Login Kit**: add the **Redirect URI** = your `tiktok-callback.html` link.
   * **Content Posting API**: turn on **Direct Post** if you want direct mode later.
4. Under **Scopes**, make sure these are included: `user.info.basic`, `video.upload`, `video.publish`.
5. To test before approval: switch to **Sandbox** at the top, create a sandbox, and add **your own TikTok
   account** as a **target user**. Sandbox has its own Client key and secret; use those.
   To post publicly with direct mode, click **Submit for review** on the production app instead.

### 5b. Save the GitHub settings

| Where | Name | Value |
|---|---|---|
| Secret | `TIKTOK_CLIENT_KEY` | Client key |
| Secret | `TIKTOK_CLIENT_SECRET` | Client secret |
| Variable | `TIKTOK_REDIRECT_URI` | Your `tiktok-callback.html` link, exactly as entered in Login Kit |

### 5c. Connect your TikTok account

1. Open your `tiktok-callback.html` page, paste your **Client key**, then click **Log in with TikTok** and approve.
2. You land back on the page, which shows a **code**. Click **Copy code**.
3. In GitHub: **Actions → Autopost → Run workflow**. Choose `tiktok-connect`, paste the code into
   **login_code**, then click **Run workflow**. Do this within a few minutes, because the code expires.
4. When the run is green, TikTok is connected. The robot keeps the TikTok login in a `_state` tab in your
   sheet and renews it automatically. **Don't edit that tab.**

> **Private repository?** GitHub Pages isn't free for private repos. Use any https page you control
> as the Redirect URI, then copy the `code=...` part from the browser's address bar after logging in.

### 5d. After TikTok approves your app (optional)

Add these GitHub **variables**: `TIKTOK_MODE` = `direct` and `TIKTOK_PRIVACY` = `PUBLIC_TO_EVERYONE`.
If you were using Sandbox keys, replace the two TikTok secrets with the production keys and repeat 5c.

---

## Step 6 — YouTube

You need a YouTube **channel** on the Google account you'll connect.

> **Important (Google's rule):** videos uploaded by a new, unaudited Google Cloud project are
> **locked as private**. To post publicly, fill in Google's free
> [YouTube API Services audit form](https://support.google.com/youtube/contact/yt_api_form).
> Describe it as "a personal tool that uploads my own videos to my own channel".
> Approval can take a few weeks. Until then everything else works, and the sheet will
> say `(YouTube set it to private)`.

### 6a. Turn on the YouTube API

Use the **same Google Cloud project** as Step 2 (<https://console.cloud.google.com/>).

1. **APIs & Services → Library**: search for **YouTube Data API v3** and click **Enable**.
2. Open **Google Auth Platform** (also called **OAuth consent screen**) and click **Get started**:
   * App name: `Autopost`. Support email: your email.
   * Audience: **External**. Contact email: your email. Then **Create**.
3. **Branding**: add your `privacy.html` link as the privacy policy and your site's main link as the home page, then **Save**.
4. **Audience**: click **Publish app** so the status becomes **In production**.
   ⚠️ If you leave it on *Testing*, Google logs the robot out every 7 days.
   You don't need Google's app verification, because you're the only user.
5. **Clients → Create client**:
   * Application type: **Web application**.
   * **Authorized redirect URIs → Add URI**: your `youtube-callback.html` link.
   * Click **Create**, then copy the **Client ID** and **Client secret**.

### 6b. Save the GitHub settings

| Where | Name | Value |
|---|---|---|
| Secret | `YOUTUBE_CLIENT_ID` | Client ID (ends in `.apps.googleusercontent.com`) |
| Secret | `YOUTUBE_CLIENT_SECRET` | Client secret |
| Variable | `YOUTUBE_REDIRECT_URI` | Your `youtube-callback.html` link, exactly as entered above |

### 6c. Connect your channel

1. Open your `youtube-callback.html` page, paste the **Client ID**, then click **Log in with Google**.
2. Choose the account or channel to upload to. If Google says **"Google hasn't verified this app"**, click
   **Advanced → Go to Autopost (unsafe)**. It's your own app. Then allow access.
3. Copy the code shown, then go to **Actions → Autopost → Run workflow**. Choose `youtube-connect`,
   paste the code into **login_code**, then click **Run workflow**.

Vertical videos under 3 minutes automatically become **YouTube Shorts**.

---

## Step 7 — Test it

In GitHub go to **Actions → Autopost → Run workflow** and run these one at a time:

1. **`init-sheet`**: creates the column headers and an example row in your sheet.
2. **`check`**: confirms every connection. Open the run to see a report like this:

   ```
   [OK] Google Sheet: 'Autopost' (time zone America/New_York)
   [OK] Facebook Page: My Page (id 1234...)
   [OK] Instagram: @mybrand (id 1784...)
   [OK] TikTok: My Name (mode: inbox)
   [OK] YouTube: My Channel (uploads as public)
   All good!
   ```

3. Add a test row with a time a few minutes ago (see below), then run **`dry-run`**.
   It shows what *would* be posted without posting anything.
4. Run **`run`** to post it for real, or just wait for the automatic run.

From now on it runs by itself every 15 minutes.

---

## Everyday use

Add one row per post:

| Column | What to put | Example |
|---|---|---|
| **Post at** | Date and time, in the **sheet's time zone** (File → Settings) | `2026-12-05 18:30` |
| **Platforms** | Any of `facebook`, `instagram`, `tiktok`, `youtube` (or `fb`, `ig`, `tt`, `yt`), or `all` | `all` |
| **Caption** | The text of the post | `New drop is live 🔥 #brand` |
| **Media** | A **Google Drive link** to a photo or video in your shared folder, or a direct link to a file. Leave empty for a text post (Facebook only) | `https://drive.google.com/file/d/…/view` |
| **Link** | *(optional)* A link to attach to a Facebook post | `https://mysite.com` |
| **Caption (Facebook / Instagram / TikTok / YouTube)** | *(optional)* A different caption for one platform. For YouTube this is the video description | |
| **Title (YouTube)** | *(optional)* The YouTube video title. If empty, the first line of the caption is used (max 100 characters) | `How we made it` |

The robot fills in the remaining columns:

| Column | Meaning |
|---|---|
| **Status** | *(empty)* = waiting · `processing` = posting now · `posted` = done · `retrying` = will try again in 15 min · `failed …` = gave up after 3 tries · `missed …` = more than 24 h late · `error …` = something in the row needs fixing |
| **Facebook / Instagram / TikTok / YouTube result** | `✅ link to the post` · `📥 sent to TikTok inbox` · `⏳ still processing` · `➖ skipped (reason)` · `❌ error message` |

Tips:

* Set **Status** to `draft` to keep a row from posting while you work on it. Clear it when the post is ready.
* **To retry after a failure:** fix the problem, then set Status to `ready`. Only the platforms that
  failed are retried; platforms that already posted are never posted twice.
* **Email alerts:** if a post fails, the GitHub run turns red and GitHub emails you.

### What each platform accepts

| | Facebook | Instagram | TikTok | YouTube |
|---|---|---|---|---|
| Text only | ✅ | ➖ needs media | ➖ needs video | ➖ needs video |
| Photo | ✅ | ✅ (PNG/HEIC converted to JPEG automatically) | ➖ skipped | ➖ skipped |
| Video | ✅ | ✅ posted as a **Reel** | ✅ | ✅ (Short if vertical and under 3 min) |

Hashtags in the caption (`#launch`) also become YouTube tags.

---

## Limits and troubleshooting

* **Timing:** GitHub sometimes starts scheduled runs late, so posts usually go out within **5–20
  minutes** of the time you set, not to the second.
* **Instagram:** photos must have an aspect ratio between 4:5 (portrait) and 1.91:1 (landscape).
  Reels can be 3 seconds to 15 minutes long. There's a daily cap on API posts per account.
* **TikTok:** there's a daily limit on posts; if you hit it, the row retries later. Before approval,
  direct mode posts are private ("only you").
* **YouTube:** the free daily API quota allows only a handful of uploads per day (roughly 6).
  If you hit it, the row retries later. Uploads stay private until Google's audit (Step 6).
* **"YouTube login failed … invalid_grant":** the Google app is still in *Testing* (Step 6a, item 4).
  Publish it, then repeat Step 6c.
* **"Meta error 190 … invalid or expired":** create a new `META_PAGE_TOKEN` (Step 4b). This happens
  if you change your Facebook password or remove the app's access.
* **"TikTok login failed / reconnect TikTok":** repeat Step 5c.
* **"Drive file not found":** the file isn't in a folder shared with the service account email.
* **A row stuck on `processing`:** a run stopped part-way. Check whether the post appeared on each
  platform, then set Status to `ready`. Platforms with an empty result cell are tried again; if one
  of them actually did post, type `✅` into its result cell first so it isn't posted twice.
* **Schedule stopped running:** GitHub pauses schedules in repositories with no activity for 60 days.
  The workflow makes a small monthly commit to prevent this. If it ever stops, open **Actions** and
  click **Enable workflow**.

## All settings

**Secrets**

| Name | Required | Purpose |
|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ✅ | Google robot account key |
| `SHEET_ID` | ✅ | Google Sheet link or ID |
| `META_PAGE_TOKEN` | for FB/IG | Facebook Page token |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` | for TikTok | TikTok app keys |
| `TIKTOK_REFRESH_TOKEN` | no | Only if you got a refresh token some other way |
| `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` | for YouTube | Google OAuth client |

**Variables** (all optional)

| Name | Default | Purpose |
|---|---|---|
| `TIKTOK_REDIRECT_URI` | — | Needed for `tiktok-connect` |
| `TIKTOK_MODE` | `inbox` | `inbox` or `direct` |
| `TIKTOK_PRIVACY` | `SELF_ONLY` | For direct mode: `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR`, `SELF_ONLY` |
| `YOUTUBE_REDIRECT_URI` | — | Needed for `youtube-connect` |
| `YOUTUBE_PRIVACY` | `public` | `public`, `unlisted` or `private` |
| `YOUTUBE_CATEGORY_ID` | `22` | YouTube category (22 = People & Blogs, 24 = Entertainment, 26 = Howto & Style) |
| `YOUTUBE_MADE_FOR_KIDS` | `false` | Set `true` if your videos are made for children |
| `TIMEZONE` | the sheet's time zone | e.g. `America/New_York` |
| `SHEET_TAB` | `Posts` | Name of the tab with your posts |
| `MAX_ATTEMPTS` | `3` | Tries before giving up |
| `MAX_LATE_HOURS` | `24` | Rows older than this are marked missed instead of posted |
| `GRAPH_API_VERSION` | `v23.0` | Meta API version |

## For developers

```bash
pip install -r requirements-dev.txt
pytest
```

Code: `autopost/runner.py` (scheduling logic), `meta.py` (Facebook + Instagram), `tiktok.py`, `youtube.py`,
`sheet.py` (Google Sheets), `media.py` (Drive downloads, JPEG conversion), `cli.py` (commands).
