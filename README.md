# GMail Cleaner — Multi-Account Fork

> **Attribution & AI-Driven Disclosure**
>
> * **Original Creator:** Forked from [**Gururagavendra/gmail-cleaner**](https://github.com/Gururagavendra/gmail-cleaner) by **Gururagavendra** `[1.1]`. All core GMail cleaning features, UI/UX, and architecture belong to the original author. If this tool helps you, please star the [original repository](https://github.com/Gururagavendra/gmail-cleaner) and consider [supporting the original creator](https://buymeacoffee.com/gururagavendra) `[1.1]`.
> * **100% AI-Driven Fork:** **All modifications, refactors, bug fixes, and updated documentation in this fork are 100% AI-driven.** Please review the source code before deploying in sensitive environments and use at your own discretion.

A fast, self-hosted, GMail-styled web app to bulk unsubscribe from newsletters, delete emails by sender or repeated subject, organize labels, and clean up multiple GMail accounts from a single Docker container `[1.1]`.

---

## Features

### Core Features (from Upstream)
* **Bulk Unsubscribe:** Scans emails for `List-Unsubscribe` headers, supports one-click RFC 8058 unsubscribe, and provides direct links for manual unsubscribes `[1.1]`.
* **Bulk Delete by Sender:** Groups senders by email count so you can trash thousands of unwanted emails in seconds (recoverable from GMail Trash for 30 days) `[1.1]`.
* **Organize & Export:** Apply/remove GMail labels, archive emails, mark/unmark as important, mark unread emails as read in bulk, and export selected senders' email metadata to CSV `[1.1]`.
* **Smart Global Filters:** Filter by *Older than*, *Custom Date Range*, *Larger than*, *Category* (Promotions, Social, Updates, Forums, Primary), *From Sender*, and *Label* `[1.1]`.

### Added in This Fork
* **Multi-Account Switcher:** Connect multiple GMail accounts via **+ Add Account**, switch between them using the header dropdown, or sign out of individual accounts. Tokens are saved per account in `./data/tokens/` with an `./data/accounts.json` index.
* **Single-Port HTTPS OAuth (`/oauth/callback`):** Removed the separate port `8767` listener. Google OAuth now completes through FastAPI at `/oauth/callback` on port `8766`, making it work cleanly over HTTPS behind **Cloudflare Tunnel** or any reverse proxy—with direct browser redirects (no hunting through Docker logs).
* **GMail API Rate-Limit Protection:** Paces batch metadata requests (`50` messages/batch with `1.0s` delay) and automatically pauses/retries with exponential backoff on `403 rateLimitExceeded` / `429` errors.
* **Group by Repeated Subject:** Toggle **Group by: `Repeated Subject`** in **Delete Emails** to normalize `Re:` / `Fwd:` prefixes and recurring calendar invite timestamps (e.g. `@ Mon Nov 23...`), grouping repeated invites/alerts/digests (2+ occurrences) and deleting only the matched emails.

---

## Quick Start

### Step 1: Google Cloud Setup (Required)
1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create or select a project `[1.1]`.
2. Enable the **GMail API** (`APIs & Services` -> `Library` -> search **GMail API** -> **Enable`) `[1.1]`.
3. Configure **Google Auth Platform** -> **Audience** -> add **every GMail address** you plan to clean under **Test users** `[1.1]`.
4. Go to **Clients** -> **Create Client** -> choose **Web application** `[1.1]`.
5. Under **Authorized redirect URIs**, add your callback URL:
   * Behind Cloudflare Tunnel / reverse proxy:
     ```text
     https://gmail.yourdomain.com/oauth/callback
     ```
   * Local access:
     ```text
     http://localhost:8766/oauth/callback
     ```
6. Download the JSON file, rename it to **`credentials.json`**, and place it in the project root `[1.1]`.

---

### Step 2: Run with Docker (Recommended)

```bash
git clone https://github.com/filtroman/gmail-cleaner-multiuser.git
cd gmail-cleaner-multiuser
mkdir -p data
# Place your credentials.json here before starting!
```

Create or update `docker-compose.yml`:
```yaml
services:
  gmail-cleaner:
    build: .
    container_name: gmail-cleaner-multiuser
    restart: unless-stopped
    ports:
      - "8766:8766"
    environment:
      - WEB_AUTH=true
      - OAUTH_REDIRECT_URI=https://gmail.yourdomain.com/oauth/callback
    volumes:
      - ./credentials.json:/app/credentials.json
      - ./data:/app/data
```

Build and start:
```bash
docker compose up -d --build
```

Open `https://gmail.yourdomain.com` (or `http://localhost:8766`) and click **Sign in with Google** `[1.1]`.

---

### Step 3: Run Locally (Without Docker)

```bash
git clone https://github.com/filtroman/gmail-cleaner-multiuser.git
cd gmail-cleaner-multiuser
# Place your credentials.json here
uv run python main.py
```

Open `http://localhost:8766` in your browser `[1.1]`.

---

## Advanced Configuration

### Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8766` | Web UI and `/oauth/callback` server port `[1.1]` |
| `WEB_AUTH` | `false` (`true` in Docker) | Enables web/headless server mode `[1.1]` |
| `OAUTH_REDIRECT_URI` | `None` (auto-detected) | Explicit callback URL (e.g. `https://gmail.yourdomain.com/oauth/callback`). If unset, auto-detects from `X-Forwarded-Proto` and `X-Forwarded-Host`. |
| `GOOGLE_CREDENTIALS` | `None` | Optional JSON string alternative to mounting `credentials.json` |

### Reverse Proxy / Cloudflare Tunnel

Only **one** hostname and **one** port (`8766`) need to be proxied:

* **Cloudflare Zero Trust -> Tunnels -> Public Hostnames:**
  * **Domain:** `gmail.yourdomain.com`
  * **Service Type:** `HTTP` *(must be `HTTP`, not `HTTPS`—Cloudflare terminates TLS at the edge and connects over plain HTTP to port `8766`)*
  * **URL:** `YOUR_DOCKER_HOST_IP:8766`
* **Nginx / Caddy / Traefik:** Proxy `https://gmail.yourdomain.com` -> `http://127.0.0.1:8766` and pass standard `X-Forwarded-Proto` / `X-Forwarded-Host` headers.

### Updating & Cache-Busting Note

```bash
cd gmail-cleaner-multiuser
git pull
docker compose up -d --build
```

Because the slim Docker image does not include `git`, static asset cache-busting falls back to `app_version` in `app/core/config.py`. When modifying files under `static/`, bump `app_version` (e.g. `2.0.1` -> `2.0.2`) or purge Cloudflare's cache so browsers do not serve cached JS/CSS.

---

## Troubleshooting

* **`Error 400: redirect_uri_mismatch`:** Verify the Authorized redirect URI in Google Cloud Console matches `OAUTH_REDIRECT_URI` character-for-character (including `https://` and `/oauth/callback`).
* **`Access blocked` / `403: access_denied`:** Add the GMail address you are trying to log into under **Google Auth Platform -> Audience -> Test users** in Google Cloud Console `[1.1]`.
* **`IsADirectoryError` on `credentials.json`:** Docker creates an empty folder if `credentials.json` didn't exist before running `docker compose up`. Run `docker compose down`, remove the empty `credentials.json` directory (`rmdir credentials.json`), copy your real JSON file there, and start again.
* **Old popup or UI changes not appearing behind Cloudflare:** Purge Cloudflare's cache or bump `app_version` in `app/core/config.py` and rebuild.

---

## Security & Privacy

* **100% Self-Hosted:** Runs directly on your machine or server—no third-party servers, telemetry, or analytics `[1.1]`.
* **Local Token Storage:** OAuth tokens are stored locally in `./data/tokens/` (only `gmail.readonly` and `gmail.modify` scopes are requested) `[1.1]`.
* **Safe Deletion:** Deleted emails are moved to GMail's **Trash** (retained 30 days), not permanently purged `[1.1]`.

---

## Tech Stack

* **Backend:** Python 3.11+, FastAPI, Uvicorn, Google API Python Client `[1.1]`
* **Frontend:** HTML5, CSS3, Vanilla JavaScript, Jinja2 templates `[1.1]`

---

## Support & License

* **Original Project:** [github.com/Gururagavendra/gmail-cleaner](https://github.com/Gururagavendra/gmail-cleaner) — [Support the original creator](https://buymeacoffee.com/gururagavendra)
* **This Fork:** Maintained by [@filtroman](https://github.com/filtroman) — [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-filtroman-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/filtroman)
* **License:** Inherits the upstream project's license — see the [`LICENSE`](LICENSE) file for details.
