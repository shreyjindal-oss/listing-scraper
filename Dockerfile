# Scrapling's official image ships with all stealth browsers pre-installed
FROM pyd4vinci/scrapling

WORKDIR /srv
# scrapling lives in the base image's /app/.venv (a uv-managed venv with no
# pip binary) — use uv to install fastapi/uvicorn into that same venv so the
# app can import both.
COPY requirements.txt .
RUN uv pip install --python /app/.venv/bin/python3 fastapi uvicorn pydantic

# Scrapling's stealth fetch drives a *patchright* Chromium (a Playwright fork);
# on Cloud Run its browser was missing → "Executable doesn't exist …
# chromium-1228 / run patchright install", so every stealth fetch failed.
# Install patchright's Chromium explicitly (the browser the error asks for) —
# do NOT gate it behind `scrapling install`, which only installs Camoufox and
# would mask the still-missing patchright browser. Also run scrapling's own
# installer (Camoufox) best-effort for the paths that use it.
RUN /app/.venv/bin/patchright install chromium --with-deps \
    || /app/.venv/bin/python -m patchright install chromium --with-deps
RUN /app/.venv/bin/python -c "from scrapling.cli import install; install([], standalone_mode=False)" || true
# Fail the build if the patchright Chromium the runtime needs isn't present,
# so a broken image never ships (this was silently reaching production before).
RUN test -x "$(ls -d /root/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1)" \
    && echo "patchright chromium OK"

# Copy the whole service/ directory (flattened into WORKDIR) instead of
# naming its files individually — a hand-maintained list silently drops new
# modules (this already broke a deploy once when alerts.py wasn't listed
# here). Anything new added under service/ just ships automatically.
COPY service/ ./

EXPOSE 8080
# Base image sets its own ENTRYPOINT (a `scrapling` CLI wrapper); override it
# so CMD actually runs, instead of being passed as args to that entrypoint.
ENTRYPOINT []
# Render (and some other hosts) inject PORT; default to 8080 elsewhere
CMD ["sh", "-c", "/app/.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
