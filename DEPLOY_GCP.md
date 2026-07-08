# Deploying the Stealth Scraper Service on Google Cloud Run

Developer runbook. The service is a Docker container (FastAPI + Scrapling
stealth browser) — Cloud Run fits it well: pay-per-use, scale-to-zero, no
servers to manage.

## Prerequisites

- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- A GCP project with billing enabled
- Roles: `roles/run.admin`, `roles/cloudbuild.builds.editor`,
  `roles/artifactregistry.admin` (or Owner/Editor)

## One-command deploy (recommended)

From the repo root (where the `Dockerfile` is — it `COPY`s in the Python
source from `service/`):

```bash
gcloud config set project YOUR_PROJECT_ID

gcloud run deploy listing-scraper-stealth \
  --source . \
  --region asia-south1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 120 \
  --concurrency 4 \
  --min-instances 0 \
  --max-instances 3 \
  --allow-unauthenticated \
  --set-env-vars API_KEY=CHOOSE_A_STRONG_KEY
```

Notes:
- `--source .` triggers Cloud Build automatically (it will offer to enable the
  Run / Cloud Build / Artifact Registry APIs on first run — say yes).
- `asia-south1` = Mumbai. Any region works.
- **2Gi memory is required** — the headless stealth browser OOMs below that.
- `--concurrency 4` keeps parallel browser sessions per instance sane.
- The first build takes ~10–15 min (the Scrapling base image is large).
- The Dockerfile already respects Cloud Run's injected `PORT` variable.

The command prints the service URL, e.g.
`https://listing-scraper-stealth-xxxxx-el.a.run.app`

## Better secret handling (optional)

Instead of a plain env var, use Secret Manager:

```bash
echo -n "CHOOSE_A_STRONG_KEY" | gcloud secrets create scraper-api-key --data-file=-

gcloud run services update listing-scraper-stealth \
  --region asia-south1 \
  --set-secrets API_KEY=scraper-api-key:latest
```

## Verify

```bash
curl https://SERVICE_URL/api/health
# → {"status":"ok"}

curl -X POST https://SERVICE_URL/api/scrape \
  -H "Content-Type: application/json" \
  -H "X-API-Key: THE_KEY" \
  -d '{"url":"https://www.airbnb.co.in/rooms/1719013680427208180?check_in=2026-07-10&check_out=2026-07-12","stealth":true}'
```

A stealth request takes 15–40 s (plus cold start on the first hit after idle).
The service also serves the test web UI at the root URL.

## Wire into the Cloudflare Worker (fallback for blocked sites)

The Worker at `cloudflare-worker/` auto-forwards blocked requests (e.g.
Booking.com, which blocks datacenter IPs on direct fetch) to this service:

```bash
cd cloudflare-worker
wrangler secret put FALLBACK_URL   # the Cloud Run service URL
wrangler secret put FALLBACK_KEY   # the API key
wrangler deploy
```

## Failure alert emails (optional)

The service emails you via SendGrid whenever a scrape hits a real fault —
blocked by anti-bot protection, fetch/parse failure (often a site markup
change), an unhandled internal error, or a page that parsed but came back
thin (missing photos/rooms/amenities). Caller mistakes (bad URLs) never alert.

```bash
echo -n "YOUR_SENDGRID_API_KEY" | gcloud secrets create sendgrid-api-key --data-file=-

gcloud run services update listing-scraper-stealth \
  --region asia-south1 \
  --set-secrets SENDGRID_API_KEY=sendgrid-api-key:latest \
  --set-env-vars ALERT_EMAIL_TO=shrey.jindal@thesqua.re,ALERT_EMAIL_FROM=noreply@thesqua.re
```

Notes:
- `ALERT_EMAIL_FROM` must be a sender verified in your SendGrid account
  (Settings → Sender Authentication), or SendGrid will reject the send.
- Repeat alerts of the same kind are throttled to one per `ALERT_COOLDOWN_S`
  (default 900s/15min) per instance, so a persistent break doesn't flood your
  inbox on every request.
- If `SENDGRID_API_KEY` isn't set, alerting silently no-ops (logged once per
  attempt) — the service still works normally, you just won't get emails.

## Continuous deployment (optional)

`cloudbuild.yaml` in the repo root builds and deploys on every push. Create a
trigger once:

```bash
gcloud builds triggers create github \
  --repo-name=listing-scraper \
  --repo-owner=shreyjindal-oss \
  --branch-pattern='^main$' \
  --build-config=cloudbuild.yaml
```

(Requires connecting the GitHub repo to Cloud Build in the console the first
time: Cloud Build → Triggers → Connect repository.)

## Known caveats

- **Booking.com may also block Google datacenter IPs.** The stealth browser
  defeats fingerprint/JS challenges, but IP-reputation blocks need a
  residential proxy. If Booking returns BLOCKED from Cloud Run, set:
  `gcloud run services update listing-scraper-stealth --set-env-vars SCRAPER_PROXY=http://user:pass@proxy:port`
  (any rotating residential proxy provider works — the scraper passes it to
  both fetchers). Airbnb works fine without this.
- Response cache is per-instance and ephemeral (`SCRAPER_CACHE_TTL`, default
  1 h). Fine for this use; move to Redis/GCS if you need shared caching.
- Keep `--max-instances` low; each instance can spawn browsers and costs
  memory-seconds.
