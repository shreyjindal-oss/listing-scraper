# Scrapling's official image ships with all stealth browsers pre-installed
FROM pyd4vinci/scrapling

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn pydantic

COPY listing_scraper.py app.py ./

EXPOSE 8000
# Render (and some other hosts) inject PORT; default to 8000 elsewhere
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
