# AJ CINE AI

**AI-powered Telegram movie & TV-series AutoFilter, identification, indexing and delivery platform.**

**Developed by AJ APPLICATIONS**
**Developer: AJWEYA**

AJ CINE AI indexes the media files in Telegram channels you are authorized to use, identifies each file
(deterministic parser → IMDb match → free-model AI fallback → human review), groups everything into a movie and
series catalog, lets users find titles with typos or Manglish, walks them through language and quality with inline
buttons, and delivers the real indexed file in a private chat. Administrators work from a dark web dashboard or a
button-driven Telegram panel.

> **Only index and deliver content you are authorized to distribute.** The operator is responsible for the sources
> they connect.

---

## Contents
1. [How it works](#how-it-works)
2. [Quick start (Docker)](#quick-start-docker)
3. [Configuration](#configuration)
4. [Using it](#using-it)
5. [OpenRouter: free models only](#openrouter-free-models-only)
6. [Title data (TMDB)](#title-data-tmdb)
7. [Deployment: VPS, Koyeb](#deployment)
8. [Backup and recovery](#backup-and-recovery)
9. [Security](#security)
10. [Tests](#tests)
11. [Known limits](#known-limits)
12. [Credits and licenses](#credits-and-licenses)

---

## How it works

```
Telegram source channels ──► Indexer (resumable jobs + live) ──► MongoDB (files)
                                      │
                                      ▼
   1. manual corrections (always win)          Identification pipeline
   2. deterministic filename parser     ──►    3. TMDB match (weighted score)
   4. OpenRouter free-model normalisation       5. review queue for anything not verified
                                      │
                                      ▼
   Catalog: Movies · Series → Seasons → Episodes → Languages → Qualities → Files
                                      │
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
   Telegram bot (search, inline navigation,        FastAPI + dashboard
   private delivery via expiring deep links,       (review, media, AI, IMDb, indexing,
   button-driven admin panel)                      users, system, backups)
```

* An AI answer is **never** trusted by itself. It only improves the search text; the accepted identification is always
  an IMDb candidate that passed the score thresholds. Uncertain results go to the review queue.
* Languages and qualities offered to users are always derived from real, non-deleted indexed files.
* Manual corrections are stored permanently and win over automatic identification on every later (re-)index.
* Nothing is downloaded: only Telegram file identifiers and source message references are stored.

## Quick start (Docker)

```bash
cp .env.example .env            # fill in the values (see Configuration)
docker compose up -d --build
```

1. Open `http://127.0.0.1:8000/admin` (the dashboard; the public website is at `/`) and sign in with `ADMIN_BOOTSTRAP_USER` / `ADMIN_BOOTSTRAP_PASSWORD`
   (the owner account is created on first start only; remove the password from the environment afterwards).
2. **IMDb → Download & import.** The first import takes up to an hour. Until it finishes, files cannot be verified
   and will wait in the review queue. (It also starts automatically when no database exists.)
3. Add the bot as an **admin** of your source channel(s). In the dashboard (**Indexing → Add source**) or the
   Telegram panel (**Admin panel → Source channels**) add the channel, then **Index history**.
4. Send the bot a movie name.

Local development without Docker: `pip install -r requirements-dev.txt`, export the variables from `.env.example`,
run `python -m ajcine` (needs a reachable MongoDB).

## Configuration

Everything is configured with environment variables. A `.env` file in the working directory (or the path in `ENV_FILE`) is read automatically at startup, and real environment variables override it. See [`.env.example`](.env.example) for the full annotated list.
Required: `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_IDS`, `MONGO_URI`, `WEB_SECRET_KEY` (≥ 32 chars). The process
refuses to start with a clear message when something required is missing. Secrets are never logged or returned by any
endpoint.

## Multiple MongoDB databases

By default everything lives in one database. To split it, set any of `MONGO_CATALOG_URI`, `MONGO_FILES_URI`,
`MONGO_USERS_URI`, `MONGO_ACTIVITY_URI`, `MONGO_ADS_URI` (a different server or cluster) and/or the matching `*_DB` names (another
database on the same server). Roles:

| Role | Holds |
|---|---|
| `catalog` | movies, series, seasons, episodes: the movie details |
| `files` | file records, review queue, corrections and decisions |
| `users` | bot users, groups the bot is in, saved lists, administrator accounts, web sessions, delivery tokens |
| `activity` | search history, deliveries, AI usage, title requests |
| `ads` | advertisements and broadcasts |
| `core` (`MONGO_URI`) | sources, indexing jobs, settings, IMDb/AI caches |

The code does not query across databases, so each can be on its own host. **Moving an existing install:** configure
the new variables, run `python -m ajcine.tools.split_db` (dry run), then `--apply` to copy the data (idempotent; the
source is never modified), verify the counts, and restart. Delete the old copies yourself once you are satisfied.
The dashboard **System** page shows each role's database, host (never credentials), health and document counts;
`/healthz` and the Telegram **System status** report a failing role. Backups read from every database.

## Collections and files

A `loc_…` (or `tt…`) id identifies **one movie or one series**: its **collection**. Each upload is a separate **file**
record that points to its collection (`collection_id`) and carries its own filename, resolution, audio language(s),
subtitles, codecs, size, source message and file id. A collection only *summarises* its files (file count, the
languages and qualities that really exist); it never stores a resolution or language that is applied to its files.

* Local collection ids are deterministic: `loc_` + a hash of the kind, normalised title and year, so the same movie
  always gets the same id. Hindi and English versions, `[Hin+Eng]` files and 480p/720p/1080p versions all join the same
  collection. A file whose name has no year joins the existing collection of that title; a remake with another year
  stays separate. Set `AUTO_LOCAL_COLLECTIONS=true` to group files IMDb cannot identify this way (default: they wait in
  the review queue so a person decides).
* Audio language comes from the file name or caption (`languages_source` records `filename`, `manual`, `ai` or
  `unknown`); subtitles are kept apart (`subtitles`). Telegram does not expose a file's audio tracks, so a language is
  never read from inside the video.
* Series are organised series → season → episode; every episode can have several languages and qualities.
* One live record per upload: a unique index on the source message, and the same upload in a second chat is stored as
  an alternative source of the first.
* **Migrate existing data safely** (nothing is deleted): `python -m ajcine.tools.collections migrate` is a dry run;
  add `--apply` to link files to collections, store match keys and merge local collections that are really the same
  title. Existing ids are kept (the largest/oldest collection survives). Inspect with
  `python -m ajcine.tools.collections report [--id ID] [--kind movie|series] [--json]`, which prints collection-level
  and file-level tables separately with fixed columns.

## Public website and streaming

`/` is a client-facing site (browse, search, title pages). Choosing a title works like the bot's inline buttons in a
group: language, then quality, then **Watch online** (in-browser streaming of the indexed Telegram file, HTTP Range
supported) or **Get in Telegram** (a link to the bot; the first Telegram account that opens it receives the file). Series add season and
episode steps. The design of the streaming is informed by TG-FileStreamBot (see THIRD_PARTY_NOTICES.md) but shares no code
with it.

* **Public site (OTT redesign):** a dark, artwork-first interface built on Bootstrap 5.3 with Bootstrap Icons, GSAP (hero and
  scroll animation, cleaned up on every page change; honours reduced motion), Swiper (shelves) and Inter from Google Fonts.
  Pages: Home (featured hero, Continue watching, Trending this week from the last 7 days of deliveries, Popular movies, Recently
  added, Top rated, Latest movies, Popular series, Anime, Recommended for you, genre collections), Movies, Series, Anime, Search
  (live suggestions, recent searches, filters, request-a-title), Title (versions and the Telegram-style key flow, seasons and
  episodes, similar titles), Watch, My library (continue watching, watchlist, history) and Preferences.
  Only real data is shown. Cast and director, episode thumbnails and skip-intro timestamps are not in the library data, so they
  are not shown; the player supports intro/outro segments and thumbnail sprites if the API ever provides them. "Anime" means
  Japanese-language Animation titles. Watchlist, history, progress and preferences are saved **in the visitor's browser**, because
  the public site has no accounts.
* **AJ CINE AI PLAYER** (`ajcine/web/site/js/player/`): modular (engine adapter, controls, timeline, settings drawer, subtitles,
  shortcuts, playback state). Direct Telegram streams use the browser's native engine with HTTP Range seeking; Shaka Player is
  loaded only for DASH/HLS manifests. Features: play/pause, previous/next episode, 10 s skip, volume, buffered + played timeline
  with hover time, speed, quality (the other real versions of the title; adaptive levels and Auto only for manifests), audio and
  subtitle tracks when the source has them, subtitle styling, loop, picture in picture, theater mode, fullscreen, resume,
  next-episode countdown, expired-link recovery (asks for a fresh signed link), media keys, touch gestures and shortcuts
  (Space/K, arrows, J/L, M, F, C, T, I, N/P, 0-9, Esc, ?).
* **Dependencies and CSP:** all CDN libraries are listed once in `ajcine/web/site/js/deps.js`, pinned and integrity-checked, with
  bundled copies in `ajcine/web/site/vendor/` used automatically when the CDN is unreachable. Only the public site may load from
  `cdn.jsdelivr.net`, `fonts.googleapis.com` and `fonts.gstatic.com`; the admin dashboard keeps a strict self-only policy.
  `PUBLIC_RATE_LIMIT_PER_MINUTE` now defaults to 240 because pages make several API calls.
* **Title requests:** when a search finds nothing, visitors can request the title. Requests are de-duplicated (repeat
  requests add a vote; the requester is stored only as a salted hash), rate-limited, and listed most-wanted first on
  the dashboard **Requests** page, which also shows when a requested title has since been indexed. Moderators mark
  them done or declined.
* Streaming is **off by default** (`STREAMING_ENABLED=false`) because it sends video through your server and uses its
  bandwidth. Turn it on only for content you are authorized to distribute. Administrators can switch the website and
  streaming off or on instantly, from the dashboard **System** page or the Telegram admin panel (**Website & streaming**),
  without restarting.
* Stream links are opaque, expire (`STREAM_TOKEN_TTL_SECONDS`) and are bound to one file; Telegram chat/message/file ids
  are never sent to the browser. Limits: `STREAM_MAX_CONCURRENT`, `STREAM_MAX_PER_IP`, `PUBLIC_RATE_LIMIT_PER_MINUTE`.
  Set `TRUST_PROXY=false` if the app is exposed without a reverse proxy.
* Set `PUBLIC_BASE_URL` (for example `https://cine.example.com`) to show a **Watch online** button in the bot's result
  messages.
* Browsers play MP4 (H.264/AAC) best. MKV, HEVC, AC3/DTS audio and similar files may not play in a given browser; the
  player says so and offers the Telegram download.

## Ads and broadcasts

**Broadcast** (dashboard *Broadcast* page or Telegram admin panel): send a text message, optionally with an image link
and one button, to bot **users**, **groups** or both. It is saved as a draft first; send yourself a test, then start.
Sending is paced (`BROADCAST_RATE_PER_SECOND`), honours Telegram flood waits, skips banned users, marks users who
blocked the bot and groups that removed it (they are not tried again), can be stopped while running, and resumes after
a restart from a stored cursor (a crash can repeat the last few messages, so delivery is at-least-once). Text is sent
escaped, never as markup. Groups are known from the moment the bot is added or someone writes there.

**Ads** (dashboard *Ads* page or Telegram admin panel): a title, text, **ad image URL**, link, button label, weight,
optional start/end dates and where it appears: under bot search results (a "Sponsored" link button), after a file is
delivered (an image message, at most once per `ADS_BOT_COOLDOWN_MINUTES` per user) and as a website banner. Images are
linked, never downloaded or stored. Impressions, clicks and click rate are counted (clicks pass through `/go/ad/<id>`).
A global switch turns all ads off. Because ad images may be hosted anywhere, the website's CSP allows `https:` images
while `ADS_ENABLED=true`, and the advertiser's host can see the visitor's address (the site sends no referrer); set
`ADS_ENABLED=false` to restore the tight TMDB-only image policy.

**More in the bot**: *My list* (save titles from any title card, `/mylist`), *Request this title* when a search finds
nothing (votes are shared with the website), `/help`, `/trending`, `/recent`. Button labels carry no emoji.
Telegram does not let bots change fonts; the website and dashboard use the self-hosted Inter and Newsreader fonts.

## Using it

**Users** (no commands to learn): send a title → pick a result → pick a language → pick a quality → tap
*Get file in private chat*. Series add season and episode steps and offer season / complete-series packs when indexed.
Missing episodes are flagged. Messages are edited in place.

**Telegram admin panel** (button on the start menu, admins only): Dashboard, Statistics, Indexing jobs, Source
channels, Broadcast, Ads, Media review, AI identification, IMDb, Users, Poster, Settings, Logs, System status. Free-text input (adding a
source, searching titles manually, banning a user) uses short conversational prompts.

**Web dashboard** (`/admin`): overview with real counts, media management (edit details, posters, change IMDb id, merge
duplicates, edit any file's season/episode/quality/language), review queue with title candidates and manual search,
correction history with undo, AI configuration and usage, TMDB status, source and job management, users and
permissions, system health, logs, backups, administrator accounts.

Roles (dashboard and Telegram): **viewer** (read) < **moderator** (review, edit media) < **admin** (indexing, sources,
AI, TMDB, users, cache) < **owner** (backups, administrators). Telegram `OWNER_IDS` are always owners.

## OpenRouter: free models only

* Set `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` and optionally `OPENROUTER_FALLBACK_MODELS` (comma separated).
  Models can also be changed at runtime in the dashboard or Telegram panel, no code change needed.
* A model is used **only if verified free** in OpenRouter's live catalogue (zero prompt *and* completion price, and no
  per-request/image/search fees). If the catalogue is unreachable, only ids ending in `:free` (or `openrouter/free`) are
  accepted. Saving a non-free model is rejected; a configured non-free model is logged and skipped, never called.
* OpenRouter's native `models` fallback array is deliberately **not** used (it can route to paid models). Fallbacks are
  tried one verified-free model at a time by this application.
* Free-tier limits are respected: local throttle (`AI_RPM`, default 15/min), `Retry-After`, exponential backoff with
  jitter, per-model circuit breakers, immediate stop on 401/402 and on the daily free limit.
* Results are cached (memory/Redis and MongoDB), and AI work runs in a slow background worker, so indexing speed never
  depends on AI. **AI can be disabled** (dashboard/Telegram) without affecting search or indexing.

## Title data (TMDB)

Titles are identified through the **TMDB API**: there is no local database, no dataset download and no import step, so
the roughly 20 GB the old IMDb/Cinemagoer database needed is gone. Set `TMDB_API_KEY` (v3 key) or `TMDB_READ_TOKEN`
(free: <https://www.themoviedb.org/settings/api>); the app refuses to start without one. A search queries TMDB's movie
and TV endpoints, fetches details for the best few results (genres, runtime, alternative titles and the **IMDb id**) and
scores them with the same weighted matcher as before. The IMDb id stays the catalog id (`tt…`), so existing titles,
files and bot buttons keep working; ratings shown are TMDB's. Lookups are cached in MongoDB for up to 30 days
(dashboard **TMDB** page: live check, cache size, clear cache).

Existing installs: you can delete `data/cinemagoer.db`, `data/imdb-datasets/` and the old `imdb_cache` collection.

Posters and backdrops also come from **TMDB**. They are stored **as URLs only** (`https://image.tmdb.org/t/p/...`); no image is downloaded or
saved on the server. A background service fills in titles without a poster: IMDb titles are looked up by IMDb id, local
(`loc_`) collections only on an exact name match with a year within one. A poster set by a person (dashboard **Set poster
URL**, or Telegram **Poster**) is never overwritten by TMDB; **Fetch from TMDB** is an explicit admin action. Posters
from older versions that were saved as files keep working. Titles without artwork show a clean typographic tile.
*This product uses the TMDB API but is not endorsed or certified by TMDB.* TMDB images load directly in the visitor's
browser from TMDB's CDN (the only external host in the site's Content-Security-Policy).

## Deployment

**VPS (Ubuntu/Debian)**
```bash
curl -fsSL https://get.docker.com | sh
git clone <your repo> ajcineai && cd ajcineai
cp .env.example .env && nano .env          # set WEB_SECURE_COOKIES=true once HTTPS works
docker compose up -d --build
docker compose logs -f app
```
Terminate TLS in front of port 8000 (the compose file binds it to localhost), for example with Caddy:
```
cine.example.com {
    reverse_proxy 127.0.0.1:8000
}
```
Open only ports 80/443. MongoDB and Redis are not published. Health check: `GET /healthz` (503 when MongoDB is down).

**Koyeb**: follow [`koyeb.yaml`](koyeb.yaml) (Dockerfile build, port 8000, health path `/healthz`, one instance, MongoDB
Atlas + Redis URL + TMDB key as secrets). There is no movie database on disk any more, so a volume is only needed if
backups must survive redeploys.

**Always run a single instance**: one bot session and one indexer. Restarts are safe: running jobs resume from their
last checkpoint, and `SIGTERM` is handled with a graceful shutdown.

## Backup and recovery

* Dashboard → System → **Create backup** (owner), or `python -m ajcine.tools.backup create` /
  `docker compose exec app python -m ajcine.tools.backup create`. Files land in `data/backups/` as
  `ajcine-<timestamp>.json.gz` and contain the catalog, users, corrections, sources, jobs and settings (not sessions,
  tokens or caches). **Backups include password hashes: treat them as secrets.**
* Restore (replaces the backed-up collections, then rebuilds indexes):
  `docker compose exec app python -m ajcine.tools.backup restore data/backups/<file>`.
* The IMDb database is not in the backup (re-downloadable). Posters are stored as URLs in the catalog, so they are included.
* For full-database disaster recovery also use `mongodump`/Atlas snapshots on a schedule.

## Security

Admin passwords use scrypt; dashboard sessions are random tokens stored only as SHA-256, in `HttpOnly`,
`SameSite=Strict` (and `Secure` over HTTPS) cookies; every state-changing call needs a custom `X-Requested-With`
header (CSRF) and a role check; login is throttled per IP and per user; a strict CSP (no inline scripts/styles) and
frame/MIME hardening are sent on every response; all request bodies are schema-validated; all dashboard output is
HTML-escaped. Telegram callback data is regex-validated (and never trusted for authorization: roles are re-checked
on every admin callback). Delivery tokens are random, stored hashed, bound to the requesting user, expire
(`TOKEN_TTL_SECONDS`), have a use limit, and are rate-limited; banned or restricted users and unidentified files are
never served. Keep `.env`, `SESSION_STRING` and backups private.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
275 automated tests cover the parser (incl. the spec examples), weighted matching, the OpenRouter free-only guarantees
(paid-model blocking, fallback, 429/402 handling, caching), the identification pipeline and manual-correction
priority, grouping of movies/series/episodes, search (typo, transliteration, filters, cache invalidation), resumable
indexing (including a simulated crash mid-job), duplicate detection, token-secured delivery, the complete Telegram
navigation flow, Telegram admin authorization, web authentication/RBAC/CSRF, dashboard statistics, backups, and an
the TMDB-backed title service (mocked HTTP: IMDb-id mapping, caching, errors, episodes). Further tests cover collection identity (Hindi/English versions, 480p/720p,
`[Hin+Eng]`, series episodes with several resolutions, duplicate Telegram messages, identical/similar filenames, the
migration), the TMDB client (mocked HTTP), HTTP Range streaming, stream/download tokens and the public API; the multi-database routing and split tool, title requests, the Telegram switches, ads (serving rules, tracking, validation), broadcasts (audiences, blocked users, flood waits, resume, cancel) and the watchlist.

## Known limits

* Live broadcasts and ad delivery through Telegram were tested only with fakes; try a broadcast to yourself first.
* Live TMDB calls and live Telegram streaming (including browser playback of real-world MKV/HEVC files) were not
  exercised either; browser playback was checked only with a small MP4 served through a fake Telegram client.
* Live Telegram, a real MongoDB server, real OpenRouter calls, a full-size IMDb import and the Docker image build were
  **not** exercised in the development sandbox; they are covered by fakes/mocks and the parts above. Do a first run
  with one small channel and check the logs.
* History indexing reads messages by id. The bot must be an admin/member of the source; channels the bot cannot read
  need `SESSION_STRING` (a user account that is a member). Telegram flood-waits are honoured automatically.
* Identification needs TMDB (a free key) and its availability: when TMDB cannot be reached, new files wait in the review
  queue instead of being guessed. A title TMDB has no IMDb id for is not offered for matching (save it as a local title).
* Ratings are TMDB's, not IMDb's. Episode titles come from TMDB when available.

## Credits and licenses

**AJ CINE AI: Developed by AJ APPLICATIONS · Developer: AJWEYA.**

Architecture ideas were studied from public projects (muse-tg-media-search-bot, Auto-Filter-Bot, Wroxen-Search,
PROFESSOR-BOT, TG-FileStreamBot); no source code was copied from them. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the
exact attributions and for the license obligations of the libraries this project uses. The GPL-licensed Cinemagoer is no longer a dependency.
