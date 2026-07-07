# Listing Scraper API — Cloudflare Worker

Serverless API version: your developer passes a property URL, the API returns
the raw listing JSON. Deployed at the edge, cached for 1 hour, no servers.

Includes a **web UI** at the root URL (served from `./public` as static
assets): paste a listing URL, hit Scrape, and inspect Summary / Raw JSON /
Photos in the browser — same experience as the local prototype, minus the
stealth-browser option (Workers can't run headless browsers). The JSON usage
docs moved to `/api/docs`.

## Deploy (PowerShell)

```powershell
cd $HOME\Desktop\listing-scraper\cloudflare-worker
npm install -g wrangler        # once
wrangler login                 # once — opens browser to authorize
wrangler deploy
```

Output shows your live URL, e.g.
`https://listing-scraper-api.<your-subdomain>.workers.dev`

### Optional: protect with an API key

```powershell
wrangler secret put API_KEY    # you'll be prompted for the value
```

Clients must then send the header `X-API-Key: <value>`.

## API for your developer

```
GET  https://<worker-url>/api/scrape?url=<URL-encoded listing URL>
POST https://<worker-url>/api/scrape        {"url": "https://…"}
GET  https://<worker-url>/api/health
GET  https://<worker-url>/                  usage docs
```

Success:
```json
{ "ok": true, "cached": false, "elapsed_s": 1.2, "data": { …listing json… } }
```

Errors (400/401/422/502):
```json
{ "ok": false, "error": "BLOCKED|UNSUPPORTED_URL|PARSE_FAILED|…", "message": "…" }
```

PowerShell test:
```powershell
$u = [uri]::EscapeDataString("https://www.airbnb.co.in/rooms/1711065697212792303?check_in=2026-07-10&check_out=2026-07-12")
Invoke-RestMethod "https://listing-scraper-api.<your-subdomain>.workers.dev/api/scrape?url=$u" | ConvertTo-Json -Depth 10
```

curl test:
```bash
curl "https://<worker-url>/api/scrape?url=https%3A%2F%2Fwww.airbnb.co.in%2Frooms%2F1711065697212792303"
```

Responses are edge-cached for 1 hour per URL — add `&no_cache=1`
(or `"no_cache": true` in POST) to force a fresh scrape.

## Important limitations vs the Python/Scrapling version

Workers can't run headless browsers, so this is the **fast-path parser**
(direct fetch + embedded-JSON parsing):

| Capability | Worker API | Python + Scrapling (parent folder) |
|---|---|---|
| Airbnb full listing data (incl. `amenities_flat`) | ✅ | ✅ |
| Airbnb **live pricing** | ❌ (JS-rendered) | ✅ with `--stealth` |
| Booking.com data + room prices | ✅ *if not blocked* | ✅ |
| Anti-bot bypass / stealth browser | ❌ | ✅ |
| Hollow-result retry + thin-amenity enrichment | ❌ (single fetch) | ✅ |
| Zero-maintenance serverless deploy | ✅ | ❌ (needs a host) |

The Worker mirrors the same JSON schema (including `amenities_flat`, the merged
deduped amenity list). It does one direct fetch per request — the retry and
amenity-enrichment logic live in the Python service, so route Booking.com
through the stealth fallback below for best coverage.

Cloudflare egress IPs are datacenter IPs — **Booking.com blocks them**
(you'll get a structured `"error": "BLOCKED"` response, not a crash).

### Stealth fallback (recommended for Booking.com)

The Worker can auto-forward blocked requests to the Python/Scrapling service
(the Docker image in the parent folder — deploy it on Fly.io / Railway /
Render / any VPS). Once it's running at a public URL:

```powershell
wrangler secret put FALLBACK_URL     # e.g. https://my-scraper.fly.dev
wrangler deploy
```

Flow: Worker tries a direct fetch (fast, free) → if blocked, it POSTs the URL
to `<FALLBACK_URL>/api/scrape` with stealth enabled and returns that result
(`"via": "stealth-fallback"` in the response). This also gets you live Airbnb
pricing on blocked/stealth requests. Optional: `wrangler secret put
FALLBACK_KEY` if you protect the fallback service.
