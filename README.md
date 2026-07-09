# Listing Scraper Prototype — Airbnb & Booking.com → JSON

Paste a property URL into a web UI (or hit the API) and get back normalized
listing JSON. Built on [Scrapling](https://github.com/D4Vinci/Scrapling).

## Quick start (PowerShell)

```powershell
cd $HOME\Desktop\listing-scraper
.\run.ps1
```

`run.ps1` creates a venv, installs dependencies, downloads the stealth browser
(first run only), and starts the server. Then open **http://localhost:8000**.

If PowerShell blocks the script:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Manual equivalent:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
scrapling install
cd service
uvicorn app:app --port 8000
```

## Using the UI

Paste any Airbnb / Booking.com listing URL (two examples are preloaded as
chips) and hit **Scrape**. Three views: **Summary** (title, rating, price,
rooms table, amenity chips, house rules), **Raw JSON** (full payload +
download), **Photos** (extracted gallery).

Tick **Stealth browser** for Airbnb when you want live pricing — Airbnb only
renders prices via JavaScript (15–40 s per fetch). Booking.com prices come
through without it. Tick **Bypass cache** to force a fresh fetch (results are
cached for 1 hour per URL otherwise).

If a listing comes back sparse, the scraper auto-retries (see *How fetching
works*); a Booking scrape that triggers enrichment simply takes a few seconds
longer.

## API

```
POST /api/scrape
{"url": "https://…", "stealth": false, "no_cache": false}

→ 200 {"ok": true, "elapsed_s": 2.1, "data": { …listing json… }}
→ 422 {"ok": false, "error": "BLOCKED|UNSUPPORTED_URL|PARSE_FAILED|…", "message": "…"}

GET /api/recent    # last 10 scrapes
GET /api/health
```

PowerShell test call:
```powershell
Invoke-RestMethod -Uri http://localhost:8000/api/scrape -Method Post `
  -ContentType "application/json" `
  -Body '{"url":"https://www.booking.com/hotel/gb/queens-park-apartments-by-flying-butler.en-gb.html?checkin=2026-07-13&checkout=2026-07-17&group_adults=2"}' |
  ConvertTo-Json -Depth 10
```

## Deploying on Google Cloud Run

See **[DEPLOY_GCP.md](DEPLOY_GCP.md)** — one-command deploy for developers
(`gcloud run deploy --source .`), plus optional CI via `cloudbuild.yaml`.

## Docker (for other hosts)

```powershell
docker build -t listing-scraper .
docker run -p 8000:8000 listing-scraper
```

Based on Scrapling's official image (browsers pre-installed) — push to
Fly.io / Railway / Render / a VPS for a shareable URL.

## Files

The Python source lives under `service/`; deployment tooling (Dockerfile,
requirements.txt, per-platform configs) stays at the repo root. The
Dockerfile does `COPY service/ ./` rather than naming files individually, so
a new module under `service/` always ships — no allowlist to keep in sync.

| File | Purpose |
|---|---|
| `service/listing_scraper.py` | Core scraper: fetch (Scrapling) + parsers + CLI |
| `service/app.py` | FastAPI server + test UI |
| `service/alerts.py` | SendGrid failure-alert emails |
| `service/test_listing_scraper.py` | offline parser tests — run after cloning to verify setup (`python test_listing_scraper.py` → `ALL TESTS PASSED`) |
| `run.ps1` | One-command PowerShell setup & run |
| `Dockerfile`, `requirements.txt` | Deployment |
| `cloudbuild.yaml`, `fly.toml`, `render.yaml` | Per-platform deploy configs |
| `cloudflare-worker/` | Serverless API version (JS) — deploy with `wrangler deploy`; see its README |

`listing_scraper.py` also works standalone (from `service/`):
`python listing_scraper.py "<url>" --stealth --pretty`

## Output schema (both platforms normalized)

```
source, url, listing_id, scraped_at
title, property_type, description
location        {address, city, country, region, postal_code, lat, lng}
host            {name, response_rate, ...}
capacity        {guests, bedrooms, beds, bathrooms}   (Airbnb; Booking is per-room)
rooms           [{name, beds, size, facilities[], options[{occupancy, price, conditions}]}]  (Booking)
amenities       [{group, items[{title, available}]}]  grouped, per-platform
amenities_flat  ["Free WiFi", "Towels", ...]           merged + deduped, available only
pricing         {currency, check_in, check_out, display_price / totals}
reviews         {rating, count, category_ratings, items[]}
photos          [{url, caption}]
house_rules, safety_and_property, cancellation_policy
```

**`amenities_flat`** is the field to map into an external amenity taxonomy: one
deduplicated list that merges room-level and building/popular facilities and
drops anything marked unavailable. The grouped `amenities` stays available for
callers who want the structure.

Photo URLs keep their `?k=…` signature token — Booking's CDN returns 401 for
requests without it. (Those signed URLs can expire over time; download and
re-host images if you need them permanently.)

## How fetching works

1. Fast path: `Fetcher.get(impersonate="chrome", stealthy_headers=True)` —
   HTTP with a real Chrome TLS fingerprint.
2. If blocked (403/429/503, challenge markers, tiny body) → automatic
   escalation to `StealthyFetcher` (fingerprint-spoofed headless Camoufox,
   bypasses Cloudflare Turnstile-class walls).
3. **Hollow-result retry** — if a page parses to no meaningful data (a bot
   interstitial that slipped past detection), the junk is purged from cache and
   the fetch is automatically retried with the stealth browser. Persistent
   failures return a clear `EMPTY_RESULT` instead of silent nulls.
4. **Thin-amenity enrichment (Booking)** — Booking A/B-serves different page
   markup; some variants carry only a few amenity badges. When the amenity
   yield looks thin, the scraper fetches once via the other mode (plain↔stealth)
   and keeps whichever variant is richer.
5. `stealth: true` skips the fast path — needed for live Airbnb pricing.

Production knobs: `SCRAPER_PROXY` (rotating residential proxy — strongly
recommended at volume; makes the retry and enrichment steps far more reliable),
`SCRAPER_CACHE_DIR`, `SCRAPER_CACHE_TTL` (default 1 h). Retries with exponential
backoff, ~1.5 s polite rate limit, structured error codes.

**Failure alert emails** (`app.py` + `alerts.py`): when a scrape is blocked,
fails to fetch/parse, comes back empty, or parses but is missing key sections
(thin content — often a markup change), the API sends a SendGrid email instead
of failing silently. See `DEPLOY_GCP.md` → "Failure alert emails" for setup
(`SENDGRID_API_KEY`, `ALERT_EMAIL_TO`, `ALERT_EMAIL_FROM`, `ALERT_COOLDOWN_S`).

## Known limitations

- Airbnb pricing requires stealth mode (client-side rendered).
- Booking.com's full facilities list loads via an IntersectionObserver — it
  never appears on page-load alone, even with JS rendering, only once that
  section scrolls into view. The stealth fetch scrolls the page to trigger it
  (see `PageFetcher._scroll_page`), so the full list is captured whenever a
  scrape escalates to stealth (either explicitly or via the thin-amenity
  retry). A plain, non-stealth fetch still only sees the small "most popular
  facilities" block, since it never runs JS at all.
- Booking.com full review texts load via JS; only featured/server-rendered
  reviews are captured.
- The thin-amenity enrichment retry helps only when the alternate fetch mode
  succeeds; if Booking blocks it that moment, re-scrape (bypass cache) a minute
  later. A proxy largely removes this.
- Both sites change markup regularly; parsers use deep key-search to tolerate
  minor changes, but expect occasional maintenance.
- Scraping may conflict with the sites' ToS — best for personal/internal
  analysis at modest volume.

## Requirements

- Python 3.10+ on PATH (`python --version`)
- ~1 GB disk for the stealth browser download
