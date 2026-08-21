# 🎬 letterboxd-overseerr-sync

[![tests](https://github.com/canmenzo/letterboxd-overseerr-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/canmenzo/letterboxd-overseerr-sync/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Everything on your **Letterboxd watchlist**, automatically requested in
**Overseerr / Jellyseerr / Seerr** — or added to your **Plex watchlist** so Overseerr's own
sync picks it up.

Films are matched by **TMDB ID**, never by title, so *Nosferatu (1922)* can never show up
when you meant *Nosferatu (2024)*. No browser, no Selenium, no third-party service.

```
Letterboxd watchlist ──▶ letterboxd-overseerr-sync ──┬──▶ Overseerr API ──▶ Radarr ──▶ your library
                                                     └──▶ Plex watchlist ──▶ Overseerr watchlist sync
```

---

## 🚀 Quick start

**1.** Your Letterboxd watchlist must be public — Letterboxd has no API, so this reads the
page like a browser would. No login, no password.

**2.** Grab your keys:

| Value | Where |
|---|---|
| `OVERSEERR_API_KEY` | Overseerr → **Settings → General → API Key** |
| `PLEX_TOKEN` *(only for the Plex route)* | [Finding a Plex auth token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |

**3.** Configure and test:

```bash
git clone https://github.com/canmenzo/letterboxd-overseerr-sync.git
cd letterboxd-overseerr-sync
cp .env.example .env
nano .env                 # LETTERBOXD_USERNAME, OVERSEERR_URL, OVERSEERR_API_KEY
mkdir -p config && sudo chown -R 1000:1000 config

docker compose run --rm letterboxd-sync --check                     # verify every endpoint
docker compose run --rm letterboxd-sync --once --dry-run --limit 5  # rehearsal
```

**4.** Let it run:

```bash
docker compose up -d
docker compose logs -f
```

With `SYNC_INTERVAL_MINUTES=360` it re-syncs every 6 hours and sleeps in between.

<details>
<summary><b>Run from cron instead, or without Docker</b></summary>

Set `SYNC_INTERVAL_MINUTES=0` so the process exits after one sync, then schedule it
(Synology Task Scheduler, or `crontab -e`):

```cron
0 */6 * * * docker compose -f /volume1/docker/letterboxd-sync/docker-compose.yml run --rm letterboxd-sync --once >> /volume1/docker/letterboxd-sync/sync.log 2>&1
```

No Docker at all:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a && . ./.env && set +a && .venv/bin/python -m letterboxd_sync --once
```

</details>

---

## 🧭 Two routes

### Route A — request directly in Overseerr (`SYNC_TARGET=overseerr`) — recommended

Immediate, and every request shows up in the Overseerr UI. Start here.

### Route B — via the Plex watchlist (`SYNC_TARGET=plex`)

Useful if you also want the watchlist mirrored into the Plex app on your TV. Two caveats:

- Overseerr's Plex Watchlist Sync runs on a randomised **~15–20 minute** cycle and
  [the interval is no longer configurable](https://github.com/sct/overseerr/issues/3754).
  Force it from **Settings → Jobs & Cache → Plex Watchlist Sync → Run now**.
- It only creates requests when **Auto-Request Movies** is enabled for that user under
  **Settings → Users → (your user) → Permissions**. Without it, films land in Discover and
  are never requested.

`SYNC_TARGET=both` does both.

---

## ⚙️ Configuration

Everything is environment variables — see [`.env.example`](.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `LETTERBOXD_USERNAME` | — | Your Letterboxd username. Syncs that user's watchlist. |
| `LETTERBOXD_LISTS` | — | Extra lists, comma separated. Full URLs, `user/list/slug`, or `user/watchlist`. |
| `SYNC_TARGET` | `overseerr` | `overseerr`, `plex`, or `both`. |
| `OVERSEERR_URL` | — | e.g. `http://192.168.1.10:5055`. Works for Overseerr, Jellyseerr and Seerr. |
| `OVERSEERR_API_KEY` | — | Settings → General → API Key. |
| `OVERSEERR_USER_ID` | API key owner | File requests under a specific Overseerr user. |
| `OVERSEERR_IS_4K` | `false` | Request the 4K version instead. |
| `PLEX_TOKEN` | — | Required when `SYNC_TARGET` includes `plex`. |
| `PRUNE_PLEX_WATCHLIST` | `false` | Remove films from the Plex watchlist when they leave Letterboxd. Only ever touches films this tool added. |
| `SYNC_INTERVAL_MINUTES` | `0` | Minutes between syncs. `0` = run once and exit. |
| `DRY_RUN` | `false` | Report what would happen, change nothing. |
| `LIMIT` | `0` | Only process the first N films. `0` = no limit. |
| `REQUEST_DELAY_SECONDS` | `1.0` | Politeness delay between Letterboxd page loads. |
| `CACHE_PATH` | `/config/letterboxd-sync.db` | Where the "already synced" database lives. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for the full story. |

### CLI flags

```
--check       Test the config and every endpoint, then exit
--once        One sync, ignore SYNC_INTERVAL_MINUTES
--dry-run     Change nothing
--force       Ignore the "already synced" cache and re-send everything
--limit N     Only process the first N films
--log-level   DEBUG | INFO | WARNING | ERROR
```

---

## 🧠 How it works

1. **Read the watchlist.** Fetches `letterboxd.com/<user>/watchlist/`, walking pages until
   it runs out. The parser accepts several generations of Letterboxd markup
   (`data-film-slug`, `data-item-slug`, `data-target-link`) and falls back to poster links.
2. **Resolve each film to a TMDB ID** by opening its Letterboxd page once and reading the
   `data-tmdb-id` / TMDb link. Everything downstream is keyed on a real ID, not a title.
3. **Cache.** Slug → TMDB ID is stored permanently in SQLite, so only genuinely new films
   cost a page load. A second table records what has already been synced per target, so a
   repeat run is nearly free.
4. **Push.** Route A checks `mediaInfo.status` first and skips anything already requested or
   already in your library, then `POST /api/v1/request`. Route B searches Plex Discover,
   **confirms the match against the item's `tmdb://` / `imdb://` GUIDs**, and only then adds
   it.

**On scraping politely.** There is no Letterboxd API. This sends one request per second by
default, identifies itself with a normal browser user-agent, backs off on HTTP 429, and —
thanks to the cache — settles into roughly one request per sync once your watchlist stops
growing. Please leave `REQUEST_DELAY_SECONDS` at 1.0 or higher.

---

<details>
<summary><b>🔎 Why this exists — the alternatives</b></summary>

| Project | What it does | Why it isn't this |
|---|---|---|
| **Overseerr / Jellyseerr / [Seerr](https://docs.seerr.dev/)** | Overseerr and Jellyseerr [merged into Seerr in Feb 2026](https://docs.seerr.dev/blog/seerr-release/). | **No native Letterboxd import.** Seerr can *link out* to Letterboxd ([issue #594](https://github.com/seerr-team/seerr/issues/594)) but cannot read a watchlist. Its only watchlist source is Plex. |
| **[ListSync](https://github.com/Woahai321/list-sync)** | Imports IMDb / Trakt / TMDB / Letterboxd lists into Overseerr & Jellyseerr. Web dashboard, Docker, many more sources than this. | The closest existing option, and genuinely good — try it first if you want the extra sources. Two differences for the Letterboxd path specifically: it drives **headless Chrome** ([SeleniumBase](https://github.com/Woahai321/list-sync/blob/main/list_sync/providers/letterboxd.py)), and its Letterboxd provider carries no TMDB id, so it skips ListSync's own direct-id lookup and falls through to **Levenshtein title + year matching** at a 0.5–0.7 similarity threshold. This tool reads the TMDB id off the Letterboxd page instead, and needs no browser. |
| **Letterboxderr** | Hosted + self-hosted Letterboxd → Seerr sync. | Closed-source frontend, third-party service between your NAS and your requests. |
| **[seerr-helper](https://chromewebstore.google.com/detail/seerr-helper/jdodogikggndnlonkmjlneoolpcckhem)** / **[letterboxd-seerr](https://github.com/MAT-GRC/letterboxd-seerr)** | Adds a "Request" button to Letterboxd film pages. | Manual, one film at a time, desktop browser only. No unattended sync. |
| **[Choff3/letterboxd-sync](https://github.com/Choff3/letterboxd-sync)**, **[treysu/letterboxd-plex-sync](https://github.com/treysu/letterboxd-plex-sync)** | Letterboxd → Plex watchlist / watched status. | Choff3's needs an external `letterboxd-list-radarr` service; treysu's targets watched status and ratings, not requests. |

</details>

<details>
<summary><b>🛠️ Troubleshooting</b></summary>

**`Found no films on … The list may be private, or Letterboxd changed its markup`**
Open the watchlist in a private browser window to confirm it's public. If it is, Letterboxd
changed its HTML — see `SLUG_PATTERNS` in `letterboxd_sync/letterboxd.py`; adding one regex
there is usually the whole fix.

**`Overseerr rejected the API key`**
Regenerate it under Settings → General. Make sure `OVERSEERR_URL` has no trailing `/api/v1`.

**`the API key's user lacks the REQUEST permission`**
Set `OVERSEERR_USER_ID` to a user that has Request permission, or grant it to the key's owner.

**Films appear in Overseerr but are never requested (route B)**
Enable **Auto-Request Movies** for your user under Settings → Users → Permissions.

**`No confident Plex Discover match`**
Plex Discover doesn't carry that film. This is intentional — it refuses to guess. Use route
A for those.

**Start over**
Delete `config/letterboxd-sync.db`, or run with `--force`.

</details>

---

## 🧪 Tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -q
```

100 tests: the parser against fixtures of both old and current Letterboxd markup,
pagination, config validation, the cache, the Overseerr client, Plex ID matching, and a
full end-to-end run of the real CLI against stub HTTP servers.

---

## 📄 License

MIT — see [LICENSE](LICENSE). Built as part of [canmenzo/NASServer](https://github.com/canmenzo/NASServer).
