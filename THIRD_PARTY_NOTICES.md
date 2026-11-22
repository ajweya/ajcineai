::: {align="center"}
# ◈ AJ CINE AI

### THIRD-PARTY NOTICES

**Developed by AJ APPLICATIONS**\
**Developer · AJWEYA**

*Independent media discovery and streaming project*

![Project](https://img.shields.io/badge/AJ%20CINE%20AI-Third--Party%20Notices-111827?style=for-the-badge)
:::

------------------------------------------------------------------------

> \[!NOTE\] AJ CINE AI is an independently developed project. AJ
> APPLICATIONS does not claim ownership of third-party projects,
> libraries, trademarks, fonts, or services referenced here. Their
> respective owners retain all rights to their materials.

## ◈ Contents

-   [01 · Reference repositories](#-01--reference-repositories)
-   [02 · Python dependencies](#-02--python-dependencies)
-   [03 · Fonts and typography](#-03--fonts-and-typography)
-   [04 · Frontend components](#-04--frontend-components)
-   [05 · External data and services](#-05--external-data-and-services)
-   [06 · Licensing and attribution](#-06--licensing-and-attribution)

------------------------------------------------------------------------

## ◈ 01 · Reference repositories

The repositories below were reviewed as architectural, functional, or
design references during development.

**No source code was copied from these repositories.** AJ CINE AI's
implementation was developed independently. Specific influences are
acknowledged below.

  ---------------------------------------------------------------------------------------------------------------------------------------------
  Repository                                                                                    License                 Review and influence
  --------------------------------------------------------------------------------------------- ----------------------- -----------------------
  [sauravr14/muse-tg-media-search-bot](https://github.com/sauravr14/muse-tg-media-search-bot)   MIT · © 2026 Saurav     Primary architecture
                                                                                                Rebhankar               reference. Reviewed its
                                                                                                                        layered filename parser
                                                                                                                        (boundary-first
                                                                                                                        parsing), resumable
                                                                                                                        history scanning,
                                                                                                                        checkpoints, supervisor
                                                                                                                        architecture,
                                                                                                                        OpenRouter title
                                                                                                                        fallback,
                                                                                                                        circuit-breaker
                                                                                                                        pattern, release-name
                                                                                                                        edge cases, and
                                                                                                                        test-suite coverage.

  [TechifyBots/Auto-Filter-Bot](https://github.com/TechifyBots/Auto-Filter-Bot)                 MIT · © 2023--2026      The reviewed snapshot
                                                                                                Techify Bots            contained a README,
                                                                                                                        license, and images,
                                                                                                                        but no source code. Its
                                                                                                                        documented feature list
                                                                                                                        informed product-scope
                                                                                                                        considerations such as
                                                                                                                        inline AutoFilter UX,
                                                                                                                        force-subscription, and
                                                                                                                        automatic deletion.

  [kynoze/Wroxen-Search](https://github.com/kynoze/Wroxen-Search)                               No license file found   Reviewed for caption
                                                                                                                        and metadata extraction
                                                                                                                        concepts, including
                                                                                                                        language markers,
                                                                                                                        quality labels, and
                                                                                                                        season/episode
                                                                                                                        identification. No code
                                                                                                                        was copied; the AJ CINE
                                                                                                                        AI parser was
                                                                                                                        independently written.

  [MrMKN/PROFESSOR-BOT](https://github.com/MrMKN/PROFESSOR-BOT)                                 AGPL-3.0                Reviewed for general
                                                                                                                        Telegram bot
                                                                                                                        functionality and
                                                                                                                        feature ideas only. No
                                                                                                                        source code was
                                                                                                                        incorporated.

  [EverythingSuckz/TG-FileStreamBot](https://github.com/EverythingSuckz/TG-FileStreamBot)       AGPL-3.0                Reviewed for the design
                                                                                                                        of HTTP streaming from
                                                                                                                        Telegram, including
                                                                                                                        signed links, HTTP
                                                                                                                        Range handling,
                                                                                                                        block-aligned reads,
                                                                                                                        and prefetching.
                                                                                                                        `ajcine/streaming.py`
                                                                                                                        was independently
                                                                                                                        implemented using the
                                                                                                                        HTTP Range
                                                                                                                        specification and the
                                                                                                                        pyrofork API.
  ---------------------------------------------------------------------------------------------------------------------------------------------

### Independent implementation

Reviewing publicly available functionality or architectural ideas does
not, by itself, mean source code has been incorporated.

AJ CINE AI maintains an independent implementation. If code from any
reference repository is incorporated in a future release, its license,
copyright notices, attribution requirements, and distribution
obligations must be reviewed before integration.

> \[!IMPORTANT\] AGPL-licensed software can impose source-code and
> network-use obligations in applicable circumstances. Do not assume
> that describing a repository as a reference removes obligations if its
> code is later incorporated.

------------------------------------------------------------------------

## ◈ 02 · Python dependencies

The following libraries are installed as dependencies through Python's
package-management ecosystem. They are not represented as original AJ
CINE AI code.

  ----------------------------------------------------------------------------------------------------------------
  Library                                                          License                 Purpose
  ---------------------------------------------------------------- ----------------------- -----------------------
  [pyrofork](https://github.com/Mayuri-Chan/pyrofork)              LGPL-3.0-or-later       Telegram MTProto
                                                                                           client; a Pyrogram
                                                                                           fork.

  [TgCrypto](https://github.com/pyrogram/tgcrypto)                 LGPL-3.0-or-later       Cryptographic
                                                                                           acceleration and
                                                                                           Telegram-related helper
                                                                                           functions.

  Motor / PyMongo                                                  Apache-2.0              MongoDB asynchronous
                                                                                           and synchronous
                                                                                           database drivers.

  [redis-py](https://github.com/redis/redis-py)                    MIT                     Redis client library.

  [FastAPI](https://fastapi.tiangolo.com/)                         MIT                     API application
                                                                                           framework.

  [Starlette](https://www.starlette.io/)                           BSD-3-Clause            ASGI framework and web
                                                                                           components.

  [Pydantic](https://docs.pydantic.dev/)                           MIT                     Data validation and
                                                                                           settings management.

  [Uvicorn](https://www.uvicorn.org/)                              BSD-3-Clause            ASGI server.

  [aiohttp](https://docs.aiohttp.org/)                             Apache-2.0              Asynchronous HTTP
                                                                                           client and server
                                                                                           functionality.

  [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)              MIT                     Fuzzy string matching.

  [python-multipart](https://github.com/Kludex/python-multipart)   Apache-2.0              Multipart form-data and
                                                                                           file-upload parsing.
  ----------------------------------------------------------------------------------------------------------------

### Test-only dependencies

-   `pytest`
-   `pytest-asyncio`
-   `mongomock-motor`
-   `fakeredis`
-   `httpx`

These are test and development dependencies rather than
application-owned source code.

------------------------------------------------------------------------

## ◈ 03 · Fonts and typography

AJ CINE AI uses self-hosted variable font files. Font assets are bundled
with the application and served directly by its server.

  ---------------------------------------------------------------------------------------------------------------------------------
  Font                                                         License                 Notice file
  ------------------------------------------------------------ ----------------------- --------------------------------------------
  [Inter](https://github.com/rsms/inter) · Variable · Latin    SIL Open Font License   `ajcine/web/site/fonts/OFL-Inter.txt`
  and Latin Extended                                           1.1                     

  [Newsreader](https://github.com/productiontype/Newsreader) · SIL Open Font License   `ajcine/web/site/fonts/OFL-Newsreader.txt`
  Variable · Latin                                             1.1                     
  ---------------------------------------------------------------------------------------------------------------------------------

The applicable font license notices are included alongside the bundled
font assets.

**Hosting note:** Self-hosted font files are served by the AJ CINE AI
server and do not require an external font host.

------------------------------------------------------------------------

## ◈ 04 · Frontend components

The public website uses the following third-party frontend libraries and
components.

  ------------------------------------------------------------------------------------------------------------------
  Component                                                              Version License          Usage
  -------------------------------------------------------- --------------------- ---------------- ------------------
  [Bootstrap](https://getbootstrap.com) · CSS and                          5.3.3 MIT              Responsive layout
  JavaScript                                                                                      and frontend
                                                                                                  utilities.

  [Bootstrap Icons](https://icons.getbootstrap.com)                       1.11.3 MIT              Interface
                                                                                                  iconography.

  [Swiper](https://swiperjs.com)                                         11.1.14 MIT              Responsive sliders
                                                                                                  and carousels.

  [GSAP](https://gsap.com) and ScrollTrigger                              3.12.5 GreenSock        Interface
                                                                                 Standard License animations and
                                                                                                  scroll-triggered
                                                                                                  effects.

  [Shaka                                                                  4.10.9 Apache-2.0       Playback of
  Player](https://github.com/shaka-project/shaka-player)                                          supported DASH and
                                                                                                  HLS manifests.
  ------------------------------------------------------------------------------------------------------------------

### CDN delivery and local fallbacks

Frontend dependencies are loaded from pinned CDN versions, including
jsDelivr and Google Fonts where applicable.

-   Subresource Integrity (SRI) hashes are used for supported CDN
    assets.
-   Where an equivalent bundled copy is available, the application can
    fall back to the local asset if the CDN resource is unavailable or
    fails its integrity check.
-   Bundled frontend license notices are maintained in
    `ajcine/web/site/vendor/`.

The original player implementation under `ajcine/web/site/js/player/`,
along with the remaining application-specific frontend code, is
independently developed as part of AJ CINE AI.

> \[!TIP\] **External connection disclosure:** CDN-hosted resources and
> Google Fonts may cause visitors' browsers to contact the corresponding
> third-party hosts. Self-hosted font files and bundled fallback assets
> are served by the AJ CINE AI server.

------------------------------------------------------------------------

## ◈ 05 · External data and services

AJ CINE AI integrates with external services for selected metadata,
identification, and AI-assisted functionality.

### 🎬 TMDB · The Movie Database

**Website:** [themoviedb.org](https://www.themoviedb.org)

TMDB is used for:

-   Movie and television title searches.
-   Title and episode details.
-   Ratings and related metadata.
-   External identifier lookups.
-   Poster and backdrop image URLs.

AJ CINE AI uses TMDB-provided image URLs. Poster and backdrop images are
not downloaded or permanently stored by the application.

TMDB is required for the metadata functionality described above and is
configured using either:

-   `TMDB_API_KEY`
-   `TMDB_READ_TOKEN`

IMDb identifiers may appear in the AJ CINE AI catalog. These identifiers
are obtained through TMDB's external-IDs data rather than through a
locally maintained IMDb dataset.

**TMDB attribution**

> This product uses the TMDB API but is not endorsed or certified by
> TMDB.

Use of the API is subject to [TMDB's Terms of
Use](https://www.themoviedb.org/api-terms-of-use). Commercial use may
require separate permission from TMDB.

### ✦ OpenRouter

**Website:** [openrouter.ai](https://openrouter.ai)

OpenRouter is used for selected AI-assisted functionality, including
title fallback where applicable.

The application is configured to use models verified as free at the time
of selection. Model availability, pricing, and provider-specific
conditions may change.

Requests processed through OpenRouter are subject to OpenRouter's terms
and the applicable model providers' policies and data-handling
practices.

### Removed metadata dependencies

The following components are no longer used by the application:

-   **Cinemagoer**
-   **Local IMDb datasets**

These dependencies were removed because maintaining the local IMDb
database would have required approximately 20 GB of storage.

IMDb identifiers remain available through TMDB's external-IDs data where
supported.

------------------------------------------------------------------------

## ◈ 06 · Licensing and attribution

Third-party libraries, fonts, frontend components, and external services
remain subject to their respective licenses and terms.

AJ CINE AI does not claim ownership of third-party materials. The
project aims to maintain applicable copyright notices, license texts,
and attribution information for bundled third-party assets.

Before distributing modified versions, packaged releases, or derivative
works, review the applicable license requirements. This is particularly
important for LGPL, AGPL, Apache-2.0, and font licenses, which can
impose different obligations depending on how their materials are used,
modified, linked, or distributed.

For questions concerning third-party attribution or licensing in AJ CINE
AI, refer to the project documentation or contact the project
maintainers.

------------------------------------------------------------------------

::: {align="center"}
### ◈ AJ CINE AI

**An independent media discovery and streaming project.**

Developed by **AJ APPLICATIONS**\
Developer · **AJWEYA**

© AJ APPLICATIONS. All rights reserved to the extent applicable to
original project materials.

*Third-party materials remain the property of their respective owners.*
:::
