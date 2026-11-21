# Third-party notices

AJ CINE AI — Developed by AJ APPLICATIONS · Developer: AJWEYA.
AJ APPLICATIONS does not claim ownership of any upstream project listed here.

## Reference repositories (studied, no code copied)

The four repositories named in the project brief were inspected. Their source was **not** copied into this project;
AJ CINE AI is an independent implementation. Where an idea clearly came from a project it is credited.

| Project | License | What was reviewed / what influenced this work |
|---|---|---|
| [sauravr14/muse-tg-media-search-bot](https://github.com/sauravr14/muse-tg-media-search-bot) | MIT, © 2026 Saurav Rebhankar | Primary architecture reference: layered filename parser ("boundary first"), resumable history scanning with checkpoints and a supervisor, OpenRouter title-fallback with a circuit breaker, release-name edge cases. Its test suite informed the edge cases covered here. |
| [TechifyBots/Auto-Filter-Bot](https://github.com/TechifyBots/Auto-Filter-Bot) | MIT, © 2023-2026 Techify Bots | The repository snapshot contains only a README, license and images (no source). Only its feature list (inline AutoFilter UX, force-subscribe, auto-delete) informed the product scope. |
| [kynoze/Wroxen-Search](https://github.com/kynoze/Wroxen-Search) | No license file found | Reviewed for its caption/metadata extraction approach (language-in-brackets, quality, season/episode). Because it has no license, nothing was copied; the parser here is written from scratch. |
| [MrMKN/PROFESSOR-BOT](https://github.com/MrMKN/PROFESSOR-BOT) | AGPL-3.0 | Reviewed for general bot feature ideas only. No code was incorporated, so AGPL obligations are not triggered. |

| [EverythingSuckz/TG-FileStreamBot](https://github.com/EverythingSuckz/TG-FileStreamBot) | AGPL-3.0 | Reviewed for the *design* of streaming Telegram files over HTTP (signed links, HTTP Range handling, block-aligned reads with prefetching). Nothing was copied; the Python implementation in `ajcine/streaming.py` was written from the HTTP Range specification and the pyrofork API. No AGPL obligations are triggered. |

If you later copy code from any of these, keep their copyright and license notices and respect their terms
(the AGPL in particular would apply to the combined work and to network users).

## Libraries used (installed via pip, not vendored)

| Library | License | Notes |
|---|---|---|
| [pyrofork](https://github.com/Mayuri-Chan/pyrofork) | LGPL-3.0-or-later | Telegram MTProto client (Pyrogram fork). |
| [TgCrypto](https://github.com/pyrogram/tgcrypto) | LGPL-3.0-or-later | Telegram crypto helpers. |
| Motor / PyMongo | Apache-2.0 | MongoDB drivers. |
| redis-py | MIT | |
| FastAPI, Starlette, Pydantic, Uvicorn | MIT / BSD | |
| aiohttp | Apache-2.0 | |
| RapidFuzz | MIT | Fuzzy matching. |
| python-multipart | Apache-2.0 | Poster uploads. |

Test-only: pytest, pytest-asyncio, mongomock-motor, fakeredis, httpx.

## Fonts (self-hosted, bundled)

| Font | License |
|---|---|
| [Inter](https://github.com/rsms/inter) variable (Latin, Latin Extended) | SIL Open Font License 1.1 (`ajcine/web/site/fonts/OFL-Inter.txt`) |
| [Newsreader](https://github.com/productiontype/Newsreader) variable (Latin) | SIL Open Font License 1.1 (`ajcine/web/site/fonts/OFL-Newsreader.txt`) |

Fonts are served from this server (no external font host).

## Public site front end (CDN, with bundled fallbacks)

| Component | Version | License |
|---|---|---|
| [Bootstrap](https://getbootstrap.com) (CSS and JS) | 5.3.3 | MIT (`ajcine/web/site/vendor/LICENSE-bootstrap.txt`) |
| [Bootstrap Icons](https://icons.getbootstrap.com) | 1.11.3 | MIT (`vendor/LICENSE-bootstrap-icons.txt`) |
| [Swiper](https://swiperjs.com) | 11.1.14 | MIT (`vendor/LICENSE-swiper.txt`) |
| [GSAP](https://gsap.com) and ScrollTrigger | 3.12.5 | GreenSock Standard License (free to use; see https://gsap.com/standard-license) |
| [Shaka Player](https://github.com/shaka-project/shaka-player) (loaded only for DASH/HLS manifests, no bundled copy) | 4.10.9 | Apache-2.0 |
| [Inter](https://rsms.me/inter/) (Google Fonts, plus a self-hosted copy) | variable | SIL Open Font License (`fonts/OFL-Inter.txt`) |

These are loaded from the jsDelivr CDN (and Google Fonts) at pinned versions with Subresource Integrity hashes, so visitors'
browsers contact those hosts. If a CDN file is blocked or fails its integrity check, the identical bundled copy in
`ajcine/web/site/vendor/` is used. The player (`js/player/`) and everything else in `ajcine/web/site/` is original code.

## Data and services

* Cinemagoer and the IMDb datasets are **no longer used** (they were removed because the local database needed about
  20 GB). IMDb ids still appear as catalog ids; they are supplied by TMDB's external-ids data.
* **OpenRouter** (<https://openrouter.ai>): only models verified as free are used; model providers' own terms apply.
* **TMDB** (<https://www.themoviedb.org>) identifies titles (search, details, ratings, episode names) and supplies poster and backdrop **URLs**; images are never downloaded or stored.
  *This product uses the TMDB API but is not endorsed or certified by TMDB.* Use of the API is subject to
  [TMDB's terms](https://www.themoviedb.org/api-terms-of-use); commercial use needs TMDB's permission. TMDB is required
  (enabled by `TMDB_API_KEY` / `TMDB_READ_TOKEN`).
