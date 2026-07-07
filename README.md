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
uvicorn app:app --port 8000
```

## Using the UI

Paste any Airbnb / Booking.com listing URL (two examples are preloaded as
chips) and hit **Scrape**. Three views: **Summary** (title, rating, price,
rooms table, amenity chips, house rules), **Raw JSON** (full payload +
download), **Photos** (extracted gallery).

Tick **Stealth browser** for Airbnb when you want live pricing — Airbnb only
renders prices via JavaScript (15–40 s per fetch). Booking.com prices come
through without it.

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

## Docker (for cloud deployment later)

```powershell
docker build -t listing-scraper .
docker run -p 8000:8000 listing-scraper
```

Based on Scrapling's official image (browsers pre-installed) — push to
Fly.io / Railway / Render / a VPS for a shareable URL.

## Files

| File | Purpose |
|---|---|
| `listing_scraper.py` | Core scraper: fetch (Scrapling) + parsers + CLI |
| `app.py` | FastAPI server + test UI |
| `test_listing_scraper.py` | 29 offline parser tests (`python test_listing_scraper.py`) |
| `run.ps1` | One-command PowerShell setup & run |
| `Dockerfile`, `requirements.txt` | Deployment |
| `cloudflare-worker/` | Serverless API version (JS) — deploy with `wrangler deploy`; see its README |

`listing_scraper.py` also works standalone:
`python listing_scraper.py "<url>" --stealth --pretty`

## Output schema (both platforms normalized)

```
source, url, listing_id, scraped_at
title, property_type, description
location   {address, city, country, lat, lng, ...}
host       {name, response_rate, ...}
capacity   {guests, bedrooms, beds, bathrooms}   (Airbnb; Booking is per-room)
rooms      [{name, beds, size, options[{occupancy, price, conditions}]}]  (Booking)
amenities  [{group, items[{title, available}]}]
pricing    {currency, check_in, check_out, display_price / totals}
reviews    {rating, count, category_ratings, items[]}
photos     [{url, caption}]
house_rules, safety_and_property, cancellation_policy
```

## How fetching works

1. Fast path: `Fetcher.get(impersonate="chrome", stealthy_headers=True)` —
   HTTP with a real Chrome TLS fingerprint.
2. If blocked (403/429/503, challenge markers, tiny body) → automatic
   escalation to `StealthyFetcher` (fingerprint-spoofed headless Camoufox,
   bypasses Cloudflare Turnstile-class walls).
3. `stealth: true` skips the fast path — needed for live Airbnb pricing.

Production knobs: `SCRAPER_PROXY` (rotating residential proxy recommended at
volume), `SCRAPER_CACHE_DIR`, `SCRAPER_CACHE_TTL` (default 1 h). Retries with
exponential backoff, ~1.5 s polite rate limit, structured error codes.

## Known limitations

- Airbnb pricing requires stealth mode (client-side rendered).
- Booking.com full review texts load via JS; only featured/server-rendered
  reviews are captured without stealth.
- Both sites change markup regularly; parsers use deep key-search to tolerate
  minor changes, but expect occasional maintenance.
- Scraping may conflict with the sites' ToS — best for personal/internal
  analysis at modest volume.

## Requirements

- Python 3.10+ on PATH (`python --version`)
- ~1 GB disk for the stealth browser download
