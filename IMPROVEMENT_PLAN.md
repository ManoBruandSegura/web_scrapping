# Project Improvement Plan

## Overview

A Leboncoin listing monitor: a Playwright scraper (`scrape_leboncoin.py`) driven by a FastAPI web app (`app.py`) with a polished vanilla-JS dashboard (`static/`), plus three legacy prototypes (`scrape.js`, `scrape_improved.js`, `scrape_leboncoin_discord.py`). The core architecture is sound — the `__NEXT_DATA__` extraction strategy is the right call and much more robust than DOM scraping, the frontend escapes HTML properly, and the background loop shuts down cleanly. The main costs today are per-query browser launches, over-conservative page waits, one live secret sitting in plaintext, and dead code.

**Assumption:** the script that matters is the web-app path (`app.py` → `scrape_leboncoin.py`); the three legacy files are superseded. "Faster" means shorter scraping cycles per query.

---

## Performance Optimizations (ordered by impact/effort)

1. **`scrape_leboncoin.py:54` — `wait_until="networkidle"` + `asyncio.sleep(2)` (line 107) + card-selector wait (line 112) are all unnecessary for the primary data path.**
   `__NEXT_DATA__` is embedded in the initial HTML server response — it exists at `domcontentloaded`, before ads, images, and trackers finish loading. `networkidle` on an ad-heavy site like Leboncoin routinely takes 10–30s or times out.
   **Change:** navigate with `wait_until="domcontentloaded"`, immediately attempt the `__NEXT_DATA__` extraction (optionally `wait_for_selector('#__NEXT_DATA__', state='attached')` with a short timeout), and only fall into the cookie-banner handling + 2s settle + card-selector wait when falling back to DOM scraping. This alone should cut each query from ~20–40s to ~3–5s.
   **Impact: High, Effort: Low.**

2. **`scrape_leboncoin.py:20` / `app.py:288` — a fresh Chromium is launched and torn down for every query, every cycle.**
   With 2 queries every 5 minutes that's ~576 browser launches/day at 2–5s each, plus memory churn.
   **Change:** restructure `scrape_leboncoin` to accept an already-open `browser` (or `context`), and have `perform_scraping_cycle` launch once, open a page per query, and close at the end of the cycle. Keep one launch per cycle rather than a permanently-open browser to avoid long-lived-session detection.
   **Impact: High, Effort: Medium.**

3. **`scrape_leboncoin.py:86-99` — cookie-banner text fallback does one `inner_text()` round-trip per button per frame.**
   Each call is a full CDP round-trip; on a page with dozens of buttons this adds seconds.
   **Change:** do the text search inside a single `frame.evaluate()` that finds and clicks the button in one call — or simply drop this whole block once fix #1 makes the cookie banner irrelevant to the primary path.
   **Impact: Medium, Effort: Low.**

4. **`app.py:422-434` — `/api/status` re-reads and re-parses `config.json` and the entire `listings.json` from disk on every poll (every 2.5s per browser tab, and it only needs the listings *count*).**
   **Change:** keep listings in memory as the source of truth (load once at startup, write-through on save), or at minimum cache both files keyed on `os.path.getmtime`. Also removes blocking file I/O from the async event loop.
   **Impact: Medium, Effort: Low.**

5. **`app.py:315` — `import re` inside the per-item filter loop.**
   Harmless after first import but pointless; move to the top-level imports and precompile the price regex.
   **Impact: Low, Effort: Low.**

6. **`app.py:341-342` — fixed 5s sleep between queries.**
   Reasonable as anti-rate-limit politeness; don't parallelize queries (concurrent hits from one IP raise Datadome suspicion). Once #1 lands, total cycle time will be dominated by this sleep, which is fine. **No change recommended** — noted so it doesn't get "optimized" away.

---

## Bugs & Correctness Risks

1. **Live Discord webhook secret exposed** — hardcoded at `scrape_leboncoin_discord.py:9` and stored in `config.json:29`.
   Anyone with that URL can post to your channel. **Rotate the webhook now**, delete the hardcoded copy, and if this folder ever becomes a git repo, add `config.json` to `.gitignore` (or move secrets to a `.env`).
   **Impact: High, Effort: Low.**

2. **`scrape_leboncoin_discord.py:26` — `scrape_leboncoin()` is called with no arguments** but the function signature (`scrape_leboncoin.py:12`) requires `config: dict`. This script crashes with a `TypeError` on its first cycle. It's superseded by `app.py`; delete it (see Quality section) rather than fix it.

3. **`app.py` — no first-run guard: the initial scrape notifies for *every* existing listing.**
   With an empty `listings.json`, all ~35 items per query are "new," producing a desktop toast and a multi-chunk Discord flood. The old `scrape_leboncoin_discord.py:16-41` handled this (`is_first_run`); the web app lost it.
   **Change:** suppress notifications when `existing_listings` was empty (or on the first cycle after a listings reset).
   **Impact: Medium, Effort: Low.**

4. **`app.py:470-485` — price sort parses French decimal prices wrong.**
   `"".join(c for c in x["price"] if c.isdigit())` turns `"1 450,00 €"` into `145000` — a 100× error that breaks price ordering whenever `__NEXT_DATA__` yields cent-precision prices (which it does, via `toLocaleString` at `scrape_leboncoin.py:133`).
   **Change:** reuse the regex-based parser already written at `app.py:317-322` (extract it into one shared function; it's currently also duplicated between the two sort branches).
   **Impact: Medium, Effort: Low.**

5. **`app.py:362-363` — `next_run_time` is computed *before* the scrape runs, but the interval sleep starts *after* it.**
   The dashboard countdown reaches zero mid-scrape and then the real run happens `scrape_duration` later; actual period is `interval + scrape_duration`.
   **Change:** set `next_run_time = now + interval` *after* `perform_scraping_cycle()` returns.
   **Impact: Low, Effort: Low.**

6. **`listings.json` grows without bound.**
   Items are never pruned — `process_scraped_listings` (`app.py:158-191`) only adds/updates. Over months this inflates every status poll, listings fetch, and save.
   **Change:** prune items whose `last_seen` is older than N days (they're delisted/sold anyway), or cap the file.
   **Impact: Medium (grows over time), Effort: Low.**

7. **`static/index.js:410` — `item.url` is interpolated into `href` unescaped** while title/price are escaped.
   Data comes from Leboncoin ad JSON, so real risk is low, but a `javascript:` or quote-containing URL would break out.
   **Change:** run it through `escapeHtml()` and/or validate it starts with `https://www.leboncoin.fr`.
   **Impact: Low, Effort: Low.**

8. **`app.py:466` — `item["title"]` in the search filter raises `KeyError` if a listing record lacks a title** (possible with hand-edited or partially-written JSON). Use `.get("title", "")`.
   **Impact: Low, Effort: Low.**

9. **`app.py:500` — `config_data.dict()` is deprecated under Pydantic v2** (which fastapi 0.111 uses); switch to `model_dump()` before an upgrade turns the warning into an error. Also the `for k, v ... current[k] = v` merge at 503-504 is just `current.update(new_config)`.
   **Impact: Low, Effort: Low.**

---

## Code Quality & Structure

1. **Delete dead files:**
   - `scrape.js` — placeholder selectors, invalid `timeoutSecondsToNetworkIdleTimeoutSeconds` option; never worked.
   - `scrape_improved.js` — puppeteer prototype superseded by the Python scraper.
   - `scrape_leboncoin_discord.py` — broken (Bugs #2), superseded by `app.py`.
   - `seen_items.json` — its migration path in `app.py:389-407` has already run (`listings.json` exists). Once deleted, the migration block in `lifespan` can go too.
   - `screenshot.png` — debug artifact.
   - `__pycache__/` — contains stale bytecode from *two different Python versions* (3.11 and 3.14); worth confirming which interpreter actually runs the app.

   **Effort: Low.**

2. **`app.py` is a 537-line single file mixing five concerns** (logging setup, persistence, notifications, scrape orchestration, API routes). Still navigable, so optional — but the natural split if it keeps growing is `storage.py` (config/listings load/save), `notify.py` (Discord + desktop toast, deduplicating the message-building logic), and `app.py` (routes + loop).
   **Effort: Medium.**

3. **Filtering logic lives in the wrong layer.** Keyword/price filtering (`app.py:290-334`) is inline in `perform_scraping_cycle`, making it untestable. Extract `filter_listings(listings, query_cfg) -> list` — it's pure logic and the one piece of this project that trivially unit-tests.
   **Effort: Low.**

4. **`urllib.request` for Discord (`app.py:133-141`) has no timeout** — a hung Discord request blocks the thread indefinitely. Add `timeout=10` to `urlopen`. (No need for `httpx`/`requests`; the stdlib approach is otherwise fine.)
   **Effort: Low.**

5. **No README / run instructions.** One paragraph covering `pip install -r requirements.txt`, `playwright install chromium`, and `uvicorn app:app` would make this resumable in six months. Note that uvicorn must run with exactly one worker (state lives in module-level dicts).
   **Effort: Low.**

6. **Already well done — leave alone:** the `__NEXT_DATA__`-first strategy with DOM fallback, sold-item filtering in the page context, HTML escaping in the frontend, capped log deque, the responsive interval sleep loop, and the debounced search. The frontend needs no framework; it's appropriate as-is.

---

## Open Questions / Assumptions

- **`config.json` has `"headless": false`** — a visible Chrome window opens every 5 minutes. Assumed this is a temporary debugging setting; the performance items above assume a return to headless.
- **Notification semantics:** should an item that disappears and reappears (relisted) re-notify? Current code never re-notifies since the URL stays in `listings.json` forever; pruning (Bugs #6) would change that. Decide before implementing pruning.
- **Anti-bot exposure:** faster, lighter page loads (Perf #1) also *reduce* the bot footprint (fewer ad/tracker requests), but if Datadome starts blocking after these changes, the fallback DOM path and its cookie handling become load-bearing again — keep them.

---

## Suggested Implementation Order

1. Bugs #1 — rotate the Discord webhook (**do this today**)
2. Perf #1 — `domcontentloaded` + immediate `__NEXT_DATA__` read
3. Perf #2 — one browser launch per cycle
4. Bugs #3 — first-run notification guard
5. Bugs #4 — shared price parser
6. Quality #1 — delete dead files
7. Everything else opportunistically.
