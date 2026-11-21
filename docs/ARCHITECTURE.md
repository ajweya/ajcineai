# Architecture

AJ CINE AI — Developed by AJ APPLICATIONS · Developer: AJWEYA

## Process model
One asyncio process (`python -m ajcine`) runs: the Pyrogram bot client (plus an optional user-session client), the
FastAPI/uvicorn server (dashboard, API, `/healthz`), and background tasks: indexing jobs, an indexing supervisor, the
AI queue worker, and the TMDB poster service. Title lookups are asynchronous HTTP calls to the TMDB API (cached in
MongoDB); there is no local movie database. Heavy work (history scans, identification) is in
background tasks, so Telegram update handling is never blocked.

## Modules (`ajcine/`)
| Module | Responsibility |
|---|---|
| `config.py`, `brand.py`, `logging_setup.py` | env configuration and validation, brand constants, structured/redacted logs |
| `db.py`, `cache.py` | Mongo collections + indexes, Redis cache with in-memory fallback and rate-limit counters |
| `parsing/` | deterministic filename parser, language detection, text normalisation, phonetic keys |
| `matching/` | candidate model + weighted matcher (`models.py`), TMDB-backed title lookup returning IMDb-keyed candidates (`service.py`) |
| `ai/` | free-only OpenRouter client (`openrouter.py`), strict validation of AI output (`identify.py`) |
| `identify/pipeline.py` | manual → parser → TMDB → AI+TMDB → review queue; applies results to files |
| `catalog.py` | titles, seasons, episodes, summaries derived from real files, merges, duplicates, stats |
| `corrections.py` | review/approve/reject/edit/merge with permanent history |
| `search.py` | token retrieval + rapidfuzz ranking, typo/phonetic/alt-title/year/type/language, cache by catalog version |
| `indexing/` | sources, live indexing, resumable jobs with checkpoints, supervisor, AI queue worker |
| `delivery.py` | user-bound expiring tokens, force-subscribe, copying the real message, missing-source handling |
| `users.py` | bot users, bans, permissions, roles, scrypt hashing |
| `bot/` | user handlers (`runner.py`), pure view helpers (`ui.py`), admin panel (`admin.py`) |
| `web/` | FastAPI app (`api.py`), the admin dashboard at `/admin` (`static/`), the public site at `/` (`site/`) and its API (`public.py`) |
| `streaming.py`, `net.py`, `site.py` | HTTP Range streaming from Telegram (1 MiB aligned reads, prefetch, limits), client IP/`TRUST_PROXY`, live site/streaming switches |
| `tmdb.py` | TMDB poster/backdrop URLs (never files); poster service honours manual posters |
| `tools/collections.py` | collection/file report with fixed columns and the safe, idempotent migration |
| `tools/backup.py` | backup/restore CLI and API helper |

## Databases
`db.MultiDB` routes `db[collection]` to one of six databases by role (`db.ROLES`): catalog, files, users, activity, ads,
core. Unset roles share the default database, so a single-database install is unchanged. There are no cross-database
queries or transactions. `tools/split_db.py` copies an existing install into the role databases.
`ads.py` (placements, weighted pick, counters), `broadcast.py` (group registry, resumable paced sender) and `watchlist.py` hold the newer services (collections `ads`, `broadcasts`, `bot_chats`, `watchlist`).

`requests.py` holds the title-request service (collection `title_requests`, role `activity`).

## Data model (MongoDB)
`users`, `administrators`, `source_channels`, `movies`, `series`, `seasons`, `episodes`, `media_files`, `languages`
(reserved), `identification_queue`, `title_cache` (TTL 30d), `manual_corrections`, `title_decisions`, `search_history`,
`delivery_history`, `delivery_tokens` (TTL on expiry), `indexing_jobs`, `system_settings`, `ai_cache` (TTL 30d),
`ai_usage`, `web_sessions` (TTL). Indexes are created on every start (`db.ensure_indexes`). File `_id` is
`"<chat_id>:<message_id>"`, which makes ingestion idempotent; `file_unique_id` detects the same upload in several chats.

## Collections and files
`movies`/`series` documents are **collections** (id `tt…` or deterministic `loc_…`); `media_files` are the files. Every
file has `title_id` and `collection_id` (same value), its own `quality`, `languages` (+`languages_source`),
`subtitles`, codecs, season/episode and source message. Collections only summarise (`file_count`, `languages`,
`qualities`). `Catalog.resolve_local_id` keeps one local collection per kind + normalised title (+ year). Indexes:
`collection_id`, `file_name`, `(collection_id, season, episode, quality)`, a unique live `file_unique_id` (partial) and
the unique source message `(source_chat_id, source_message_id)`.

## Public site and streaming
`/api/public/*` is read-only and rate-limited. The browser only knows catalog ids and opaque tokens. `POST
/api/public/stream-link` picks a file server-side and issues an expiring stream token (hash stored in
`delivery_tokens`, kind `stream`); `GET|HEAD /stream/{token}` resolves the Telegram message (primary source, then
duplicates), answers 200/206/416 and streams through `StreamLimiter`. `POST /api/public/download-link` issues a
download token with no user yet; the first Telegram account to open `t.me/<bot>?start=dl_<token>` claims it atomically.
Security headers are a pure ASGI middleware (it does not interfere with streamed bodies).

## Identification confidence
`score = 0.55·title + 0.20·year + 0.10·type + 0.05·original-title + 0.10·metadata`, then capped: title similarity
< 0.6 → ≤ 0.5, year mismatch > 1 → ≤ 0.70, type mismatch → ≤ 0.60. `>= MATCH_AUTO_THRESHOLD` (and no near-equal rival)
→ approved; `>= MATCH_REVIEW_THRESHOLD` → review queue; otherwise unidentified. An AI-normalised title must still win
this IMDb scoring to be approved.

## Indexing and recovery
Each job stores `cursor`; a batch is ingested, then the cursor/counters are written. On restart `resume()` re-queues
`running` jobs from the stored cursor; ingestion is idempotent so a re-processed batch creates no duplicates. A
supervisor restarts jobs whose task died or whose heartbeat is older than 10 minutes. Flood-waits sleep for the
requested time; losing access to a source fails the job with a clear message instead of retrying forever.
