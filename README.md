# 🎬 letterboxd-overseerr-sync

[![tests](https://github.com/canmenzo/letterboxd-overseerr-sync/actions/workflows/tests.yml/badge.svg)](https://github.com/canmenzo/letterboxd-overseerr-sync/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Everything on your **Letterboxd watchlist**, automatically requested in **Seerr** (formerly
Overseerr). Seerr can't read a Letterboxd watchlist — its only watchlist source is Plex.
This closes that gap.

Films are matched by **TMDB ID**, never by title, so *Nosferatu (1922)* can never show up
when you meant *Nosferatu (2024)*. No browser, no third-party service.

```
Letterboxd watchlist ──▶ letterboxd-overseerr-sync ──▶ Seerr ──▶ Radarr ──▶ your library
```

---

## 🚀 Quick start

**1.** Your Letterboxd watchlist must be public — Letterboxd has no API, so this reads the
page like a browser would. No login, no password.

**2.** Get your API key: Seerr → **Settings → General → API Key**. Its user needs
**Request** permission.

**3.** Configure and test:

```bash
git clone https://github.com/canmenzo/letterboxd-overseerr-sync.git
cd letterboxd-overseerr-sync
cp .env.example .env
nano .env                 # LETTERBOXD_USERNAME, OVERSEERR_URL, OVERSEERR_API_KEY
mkdir -p config

docker compose run --rm letterboxd-sync --check                     # verify every endpoint
docker compose run --rm letterboxd-sync --once --dry-run --limit 5  # rehearsal
```

**4.** Let it run:

```bash
docker compose up -d
docker compose logs -f
```

<details>
<summary><b>Run from cron instead, or without Docker</b></summary>

Set `SYNC_INTERVAL_MINUTES=0` so the process exits after one sync, then schedule it
(Synology Task Scheduler, or `crontab -e`):

```cron
*/15 * * * * docker compose -f /path/to/letterboxd-overseerr-sync/docker-compose.yml run --rm letterboxd-sync --once >> /path/to/sync.log 2>&1
```

No Docker at all:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a && . ./.env && set +a && .venv/bin/python -m letterboxd_sync --once
```

</details>

---

## ⚙️ Configuration

Environment variables — see [`.env.example`](.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `LETTERBOXD_USERNAME` | — | Your Letterboxd username. Several can be comma separated. |
| `LETTERBOXD_LISTS` | — | More people and more lists, comma separated. A bare username means that user's watchlist; also accepts `user/list/slug` and full URLs. Films on several lists are requested once. |
| `OVERSEERR_URL` | — | e.g. `http://192.168.1.10:5055`. No trailing `/api/v1`. |
| `OVERSEERR_API_KEY` | — | Settings → General → API Key. |
| `OVERSEERR_USER_ID` | API key owner | File requests under a specific Seerr user. |
| `OVERSEERR_IS_4K` | `false` | Request the 4K version instead. Needs a 4K server configured in Seerr. |
| `SYNC_INTERVAL_MINUTES` | `0` | Minutes between syncs. `0` = run once and exit. `15` is comfortable — see below. |
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
--force       Ignore the "already synced" cache and re-read the entire watchlist
--limit N     Only process the first N films
--log-level   DEBUG | INFO | WARNING | ERROR
```

---

## 🧠 How it works

1. **Read the watchlist.** Walks `letterboxd.com/<user>/watchlist/` page by page. The parser
   accepts several generations of Letterboxd markup and falls back to poster links.
2. **Stop early.** A watchlist is ordered newest-addition-first, so once a whole page holds
   nothing but films already handled, everything below is older and paging stops. A routine
   sync is **one request**, which is why a 15-minute interval is reasonable. `--force`
   re-reads everything.
3. **Resolve to a TMDB ID** from each new film's Letterboxd page. Everything downstream is
   keyed on a real ID, not a title. The TMDb link is read in preference to the page's
   `data-tmdb-id` attribute, which disagrees with it for entries TMDB files as a series.
4. **Cache.** Slug → TMDB ID is stored permanently in SQLite, so a film costs one page load
   ever. A second table records what has already been requested.
5. **Request.** Checks `mediaInfo.status` first and skips anything already requested or
   already in your library, then `POST /api/v1/request`.

**On scraping politely.** There is no Letterboxd API. This sends one request per second by
default, identifies itself with a normal browser user-agent, and backs off on HTTP 429.
Please leave `REQUEST_DELAY_SECONDS` at 1.0 or higher.

---

<details>
<summary><b>🛠️ Troubleshooting</b></summary>

**`returned HTTP 403`**
Letterboxd answers non-canonical URLs with a Cloudflare challenge. Page one is always
requested with a trailing slash for this reason — if you see a 403, check that the watchlist
is public by opening it in a private browser window.

**`Found no films on … The list may be private, or Letterboxd changed its markup`**
If the list is public, Letterboxd changed its HTML — see `SLUG_PATTERNS` in
`letterboxd_sync/letterboxd.py`; adding one regex there is usually the whole fix.

**`Overseerr rejected the API key`**
Regenerate it under Settings → General. Check `OVERSEERR_URL` has no trailing `/api/v1`.

**`the API key's user lacks the REQUEST permission`**
Set `OVERSEERR_USER_ID` to a user that has Request permission, or grant it to the key's owner.

**Requests show as Failed in Seerr**
Usually a timeout, not a rejection — Seerr gives Radarr 10 seconds to respond, and Radarr
gets slow when hundreds of requests arrive at once with search-on-add enabled. The films
generally land in Radarr anyway. Turn off **Enable Automatic Search** in
**Settings → Services → Radarr** and search in batches instead.

**Start over**
Delete `config/letterboxd-sync.db`, or run with `--force`.

</details>

---

## 🧪 Tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -q
```

74 tests: the parser against fixtures of old and current Letterboxd markup, pagination and
early-stop, config validation, the cache, the Seerr client, and a full end-to-end run of the
real CLI against stub HTTP servers.

---

## 📄 License

MIT — see [LICENSE](LICENSE). Built as part of [canmenzo/NASServer](https://github.com/canmenzo/NASServer).
