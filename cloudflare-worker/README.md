# Listing Scraper API — Cloudflare Worker

Serverless API version: your developer passes a property URL, the API returns
the raw listing JSON. Deployed at the edge, cached for 1 hour, no servers.

Includes a **web UI** at the root URL (served from `./public` as static
assets): paste a listing URL, hit Scrape, and inspect Summary / Raw JSON /
Photos in the browser. The JSON usage docs are at `/api/docs`.

**Stealth mode** is built in via Cloudflare **Browser Rendering** (managed
Chromium) — enabled by the `[browser]` binding in `wrangler.toml`. It renders
JavaScript (so it captures Airbnb live pricing) and gets past bot walls that
block a plain fetch. A blocked direct fetch **auto-escalates** to Browser
Rendering; each response's `via` field reports the path used
(`direct-fetch` | `browser-rendering` | `stealth-fallback`).

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
GET  https://<worker-url>/api/scrape?url=<URL-encoded listing URL>[&stealth=1][&no_cache=1]
POST https://<worker-url>/api/scrape        {"url": "https://…", "stealth": false, "no_cache": false}
GET  https://<worker-url>/api/health
GET  https://<worker-url>/api/docs          usage docs (JSON)
```

`stealth=1` forces Browser Rendering (JS-rendered pages, anti-bot). Without it
the Worker does a fast direct fetch and only escalates to Browser Rendering if
that fetch is blocked.

Success:
```json
{ "ok": true, "cached": false, "via": "direct-fetch", "elapsed_s": 1.2, "data": { …listing json… } }
```

Errors (400/401/422/501/502):
```json
{ "ok": false, "error": "BLOCKED|UNSUPPORTED_URL|STEALTH_UNAVAILABLE|…", "message": "…" }
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

## Stealth mode (Browser Rendering)

The `[browser]` binding in `wrangler.toml` gives the Worker a managed Chromium
via Cloudflare Browser Rendering — no extra setup beyond the binding (it's on
the Workers free tier, with usage limits). It's what makes `stealth=1` and the
auto-escalation on blocked fetches work.

- Requires `compatibility_date >= 2026-03-24` (already set).
- Local testing must use `wrangler dev --remote` (Browser Rendering doesn't run
  in local mode).
- If you remove the binding, `stealth=1` returns `501 STEALTH_UNAVAILABLE` and
  the direct fetch path still works.

## Capabilities vs the Python/Scrapling version

| Capability | Worker API | Python + Scrapling |
|---|---|---|
| Airbnb full listing data (incl. `amenities_flat`) | ✅ | ✅ |
| Airbnb **live pricing** | ✅ with `stealth=1` | ✅ with `--stealth` |
| Booking.com data + room prices | ✅ (stealth recommended) | ✅ |
| Anti-bot bypass / stealth browser | ✅ Browser Rendering | ✅ Camoufox |
| Hollow-result retry + thin-amenity enrichment | ❌ (single fetch) | ✅ |
| Zero-maintenance serverless deploy | ✅ | ❌ (needs a host) |

Both share the same JSON schema (including `amenities_flat`). The extra
hollow-result retry and thin-amenity enrichment logic still live only in the
Python service; for the richest Booking.com coverage at volume, either use
`stealth=1` here or route through the external fallback below.

Cloudflare egress IPs are datacenter IPs — **Booking.com blocks a plain fetch**
from them, so a non-stealth Booking request returns `"error": "BLOCKED"` (or
auto-escalates to Browser Rendering when the binding is present).

### Optional: external Python/Scrapling fallback

Built-in Browser Rendering (above) is the simplest stealth path. This external
fallback is an *alternative* — use it if you'd rather not enable Browser
Rendering, or you want the Python service's extra retry + thin-amenity
enrichment. Deploy the Docker image in the parent folder (Cloud Run / any VPS),
then:

```powershell
wrangler secret put FALLBACK_URL     # e.g. https://my-scraper.example.com
wrangler deploy
```

Flow: Worker tries a direct fetch → if blocked and no `[browser]` binding
handled it, it POSTs the URL to `<FALLBACK_URL>/api/scrape` with stealth enabled
and returns that result (`"via": "stealth-fallback"`). Optional: `wrangler secret put
FALLBACK_KEY` if you protect the fallback service.
