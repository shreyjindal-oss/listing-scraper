# Scrapling's official image ships with all stealth browsers pre-installed
FROM pyd4vinci/scrapling

WORKDIR /srv
# scrapling lives in the base image's /app/.venv (a uv-managed venv with no
# pip binary) — use uv to install fastapi/uvicorn into that same venv so the
# app can import both.
COPY requirements.txt .
RUN uv pip install --python /app/.venv/bin/python3 fastapi uvicorn pydantic

# The base image bundles browsers, but the stealth browser build
# (patchright/Camoufox) can drift from the installed Scrapling version — on
# Cloud Run that surfaced as "Executable doesn't exist … chromium-1228 / run
# patchright install", so every stealth fetch failed. Install the exact
# browsers this Scrapling needs so StealthyFetcher can actually launch.
# Try Scrapling's own installer first; fall back to patchright's chromium.
RUN /app/.venv/bin/python -c "from scrapling.cli import install; install([], standalone_mode=False)" \
    || /app/.venv/bin/python -m patchright install chromium --with-deps

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
