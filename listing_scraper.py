#!/usr/bin/env python3
"""
listing_scraper.py — Scrape Airbnb / Booking.com listing pages into normalized JSON.
Built on Scrapling (https://github.com/D4Vinci/Scrapling).

Usage:
    python listing_scraper.py <URL> [--pretty] [--output out.json] [--no-cache] [--stealth]

    --stealth uses Scrapling's StealthyFetcher (headless Camoufox browser):
      * bypasses anti-bot walls (Cloudflare, PerimeterX, ...)
      * renders JavaScript — enables live Airbnb pricing extraction
    Without it, a fast TLS-impersonated HTTP request is used and the scraper
    auto-escalates to stealth if the plain request gets blocked.

Env vars:
    SCRAPER_PROXY      e.g. http://user:pass@proxy:8080
    SCRAPER_CACHE_DIR  default: .scraper_cache
    SCRAPER_CACHE_TTL  seconds, default 3600

Install:
    pip install "scrapling[fetchers]"
    scrapling install          # downloads the stealth browser (needed for --stealth)
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

from scrapling.parser import Selector

BLOCK_MARKERS = [
    "captcha", "px-captcha", "are you a robot", "access denied",
    "unusual traffic", "challenge-platform", "cf-chl",
]

MONEY_RE = re.compile(r"([₹$€£]|USD|EUR|GBP|INR)\s?([\d,]+(?:\.\d+)?)")


class ScraperError(Exception):
    """Raised for fetch/parse failures with a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# Fetching layer (Scrapling): impersonated HTTP → stealth-browser escalation
# --------------------------------------------------------------------------- #

class PageFetcher:
    def __init__(self, cache_dir: Optional[str] = None, cache_ttl: int = 3600,
                 max_retries: int = 3, min_interval: float = 1.5):
        self.proxy = os.environ.get("SCRAPER_PROXY") or None
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self.min_interval = min_interval  # polite rate limit between requests
        self._last_request_ts = 0.0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # -- cache ----------------------------------------------------------------

    def _cache_path(self, url: str) -> Optional[str]:
        if not self.cache_dir:
            return None
        return os.path.join(self.cache_dir, hashlib.sha256(url.encode()).hexdigest() + ".html")

    def _from_cache(self, url: str) -> Optional[str]:
        p = self._cache_path(url)
        if p and os.path.exists(p) and (time.time() - os.path.getmtime(p)) < self.cache_ttl:
            with open(p, encoding="utf-8") as f:
                return f.read()
        return None

    def _to_cache(self, url: str, text: str) -> None:
        p = self._cache_path(url)
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)

    def invalidate(self, url: str, stealth: bool = False) -> None:
        """Drop a cached page (e.g. when it parsed to nothing)."""
        p = self._cache_path(url + ("#stealth" if stealth else ""))
        if p and os.path.exists(p):
            os.remove(p)

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed + random.uniform(0, 0.5))

    # -- fetch strategies -------------------------------------------------------

    @staticmethod
    def _page_html(page: Any) -> str:
        html = getattr(page, "html_content", None) or getattr(page, "body", None) or str(page)
        if isinstance(html, bytes):
            html = html.decode("utf-8", "replace")
        return html

    def _looks_blocked(self, page: Any, html: str) -> bool:
        status = getattr(page, "status", 200)
        if status in (403, 429, 503):
            return True
        low = html[:20000].lower()
        return len(html) < 5000 or any(m in low for m in BLOCK_MARKERS)

    def _http_fetch(self, url: str) -> Optional[str]:
        """Fast path: Scrapling Fetcher with Chrome TLS-fingerprint impersonation."""
        from scrapling.fetchers import Fetcher
        kwargs = {"impersonate": "chrome", "stealthy_headers": True, "timeout": 30}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        page = Fetcher.get(url, **kwargs)
        html = self._page_html(page)
        if self._looks_blocked(page, html):
            return None
        return html

    def _stealth_fetch(self, url: str) -> str:
        """Slow path: headless stealth browser. Renders JS (Airbnb pricing)."""
        from scrapling.fetchers import StealthyFetcher
        kwargs = {"headless": True, "network_idle": True}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        page = StealthyFetcher.fetch(url, **kwargs)
        html = self._page_html(page)
        if self._looks_blocked(page, html):
            raise ScraperError("BLOCKED", "Blocked even with the stealth browser — "
                                          "try SCRAPER_PROXY with a residential proxy")
        return html

    def get(self, url: str, use_cache: bool = True, stealth: bool = False) -> str:
        cache_key = url + ("#stealth" if stealth else "")
        if use_cache:
            cached = self._from_cache(cache_key)
            if cached is not None:
                return cached

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            self._throttle()
            self._last_request_ts = time.time()
            try:
                if stealth:
                    html = self._stealth_fetch(url)
                else:
                    html = self._http_fetch(url)
                    if html is None:  # blocked → escalate to stealth browser
                        try:
                            html = self._stealth_fetch(url)
                        except ImportError:
                            raise ScraperError(
                                "BLOCKED",
                                "Plain request was blocked and the stealth browser isn't "
                                "installed. Run: pip install 'scrapling[fetchers]' && scrapling install")
                self._to_cache(cache_key, html)
                return html
            except ScraperError as e:
                last_err = e
            except Exception as e:  # network hiccups etc.
                last_err = e
            time.sleep((2 ** attempt) + random.uniform(0, 1.5))

        if isinstance(last_err, ScraperError):
            raise last_err
        raise ScraperError("FETCH_FAILED", f"Failed to fetch {url}: {last_err}")


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #

def deep_find_all(obj: Any, key: str, max_depth: int = 30) -> list:
    """Return all non-null values for `key` anywhere in a nested structure."""
    out = []

    def walk(o, depth):
        if o is None or depth > max_depth:
            return
        if isinstance(o, list):
            for x in o:
                walk(x, depth + 1)
        elif isinstance(o, dict):
            for k, v in o.items():
                if k == key and v is not None:
                    out.append(v)
                walk(v, depth + 1)

    walk(obj, 0)
    return out


def deep_find(obj: Any, key: str) -> Any:
    hits = deep_find_all(obj, key)
    return hits[0] if hits else None


def strip_html(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return re.sub(r"<[^>]+>", "", html_lib.unescape(s)).strip()


def flatten_amenities(groups: list) -> list:
    """Single deduped list of available amenity titles across all groups —
    handy for mapping into an external amenity taxonomy."""
    flat, seen = [], set()
    for g in groups or []:
        for item in g.get("items", []):
            if item.get("available") is False:
                continue
            t = (item.get("title") or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                flat.append(t)
    return flat


def text_of(el) -> str:
    """innerText-ish: join all descendant text nodes of a Scrapling element."""
    if el is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(el.css("::text").getall())).strip()


def first_text(page, css: str) -> Optional[str]:
    el = page.css_first(css) if hasattr(page, "css_first") else None
    if el is None:
        found = page.css(css)
        el = found[0] if found else None
    t = text_of(el)
    return t or None


# --------------------------------------------------------------------------- #
# Airbnb parser
# --------------------------------------------------------------------------- #

class AirbnbParser:
    """Parses the Airbnb PDP (server-rendered or stealth-browser-rendered).

    All listing data lives in <script id="data-deferred-state-0"> as JSON:
        niobeClientData[0][1].data.presentation.stayProductDetailPage.sections
    We deep-search by key so minor structural changes don't break us.

    Pricing: Airbnb renders prices client-side — the SSR payload carries
    structuredDisplayPrice: null. When the page was fetched with the stealth
    browser (--stealth), the rendered book-it sidebar DOM is parsed for the
    live price instead.
    """

    def parse(self, html: str, url: str) -> dict:
        page = Selector(html)
        state = self._embedded_state(page)
        ld = self._ld_json(page)

        sections = {}
        try:
            pdp = state["niobeClientData"][0][1]["data"]["presentation"]["stayProductDetailPage"]
            for s in pdp.get("sections", {}).get("sections", []):
                sections.setdefault(s.get("sectionComponentType"), []).append(s.get("section") or {})
        except (KeyError, IndexError, TypeError):
            pass

        root = state if state else {}
        qs = parse_qs(urlparse(url).query)

        listing_id = re.search(r"/rooms/(\d+)", url)
        amenities = self._amenities(root)

        return {
            "source": "airbnb",
            "url": url.split("?")[0],
            "listing_id": listing_id.group(1) if listing_id else None,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "title": self._title(sections, page, root),
            "property_type": self._property_type(page, ld),
            "description": self._description(sections, root, page),
            "location": self._location(root, ld, page),
            "host": self._host(sections, root),
            "capacity": self._capacity(root, page),
            "sleeping_arrangement": self._sleeping(root),
            "amenities": amenities,
            "amenities_flat": flatten_amenities(amenities),
            "pricing": self._pricing(root, page, qs),
            "reviews": self._reviews(root, ld),
            "photos": self._photos(root),
            "house_rules": self._house_rules(sections, root)[0],
            "safety_and_property": self._house_rules(sections, root)[1],
            "cancellation_policy": self._house_rules(sections, root)[2],
        }

    # -- extraction pieces ---------------------------------------------------

    def _embedded_state(self, page) -> dict:
        for sel in ('script[id^="data-deferred-state"]::text',
                    "script#data-injector-instances::text"):
            raw = page.css(sel).get()
            if raw:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    continue
        raise ScraperError("PARSE_FAILED", "No embedded state JSON found on Airbnb page "
                                           "(layout changed, or the page was bot-gated)")

    def _ld_json(self, page) -> dict:
        for raw in page.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") in (
                        "VacationRental", "LodgingBusiness", "Product", "Place", "House"):
                    return item
        return {}

    def _title(self, sections, page, root=None) -> Optional[str]:
        """Fallback chain, most reliable listing-name sources first.

        og:description carries the listing name on Airbnb; og:title holds the
        "Rental unit in X · ★4.7 · …" share summary, so it goes last.
        """
        def share_summary(s):
            return bool(re.search(r"·\s*(?:★|\d+\s*bed)", s or ""))

        for sec in sections.get("TITLE_DEFAULT", []):
            if sec.get("title"):
                return sec["title"]
        t = first_text(page, "h1")
        if t:
            return t
        # <title> looks like: "Sundeck | Cozy ... - Flats for Rent in ... - Airbnb"
        t = page.css("title::text").get()
        if t and t.strip():
            head = t.strip().split(" - ")[0].strip()
            if not re.match(r"^Airbnb[:\s]", head):
                return head
        og_desc = page.css('meta[property="og:description"]::attr(content)').get()
        if og_desc and len(og_desc) <= 120 and not share_summary(og_desc):
            return og_desc.strip()
        for t in deep_find_all(root or {}, "listingTitle"):
            if isinstance(t, str) and t.strip() and not share_summary(t):
                return t
        for s in deep_find_all(root or {}, "sharingConfig"):
            if isinstance(s, dict) and s.get("title"):
                return s["title"]
        og = page.css('meta[property="og:title"]::attr(content)').get()
        return og.split("·")[0].strip() if og else None

    def _property_type(self, page, ld) -> Optional[str]:
        # og:title looks like: "Rental unit in Noida · ★5.0 · 2 bedrooms ..."
        og = page.css('meta[property="og:title"]::attr(content)').get()
        if og:
            head = og.split("·")[0].strip()
            if " in " in head:
                return head.split(" in ")[0].strip()
        return ld.get("@type")

    def _description(self, sections, root, page) -> Optional[str]:
        for key in ("htmlDescription", "descriptionOriginal"):
            for hit in deep_find_all(root, key):
                text = hit.get("htmlText") if isinstance(hit, dict) else hit
                if isinstance(text, str) and len(text) > 40:
                    return strip_html(text.replace("<br />", "\n").replace("<br/>", "\n"))
        return page.css('meta[name="description"]::attr(content)').get()

    def _capacity(self, root, page) -> dict:
        cap = {"guests": deep_find(root, "personCapacity"),
               "bedrooms": None, "beds": None, "bathrooms": None}
        og = page.css('meta[property="og:title"]::attr(content)').get() or ""
        blob = og + " " + json.dumps(deep_find_all(root, "sharingConfig") or [])
        for field, pat in (("bedrooms", r"(\d+(?:\.\d+)?)\s*bedroom"),
                           ("beds", r"(\d+)\s*bed(?!room)"),
                           ("bathrooms", r"(\d+(?:\.\d+)?)\s*(?:private\s+)?bath")):
            m = re.search(pat, blob, re.I)
            if m:
                v = float(m.group(1))
                cap[field] = int(v) if v.is_integer() else v
        return cap

    def _sleeping(self, root) -> list:
        out, seen = [], set()
        for arr in deep_find_all(root, "arrangementDetails") + deep_find_all(root, "sleepingArrangements"):
            for item in arr if isinstance(arr, list) else [arr]:
                if isinstance(item, dict) and item.get("title"):
                    k = (item.get("title"), item.get("subtitle"))
                    if k not in seen:
                        seen.add(k)
                        out.append({"room": item.get("title"), "beds": item.get("subtitle")})
        return out

    def _amenities(self, root) -> list:
        groups = deep_find(root, "seeAllAmenitiesGroups") or deep_find(
            root, "previewAmenitiesGroups") or []
        out = []
        for g in groups:
            items = [{"title": a.get("title"),
                      "subtitle": (a["subtitle"].get("text") if isinstance(a.get("subtitle"), dict)
                                   else a.get("subtitle")) or None,
                      "available": a.get("available", True)}
                     for a in g.get("amenities", [])]
            if items:
                out.append({"group": g.get("title"), "items": items})
        return out

    def _photos(self, root) -> list:
        urls, seen = [], set()
        for item in deep_find_all(root, "mediaItems"):
            for m in item if isinstance(item, list) else [item]:
                if isinstance(m, dict):
                    u = m.get("baseUrl")
                    if u and u not in seen:
                        seen.add(u)
                        urls.append({"url": u,
                                     "caption": strip_html(m.get("accessibilityLabel") or "") or None})
        return urls

    def _location(self, root, ld, page) -> dict:
        loc = {"address": None, "city": None, "country": None, "lat": None, "lng": None}
        lat, lng = deep_find(root, "lat"), deep_find(root, "lng")
        if isinstance(lat, (int, float)):
            loc["lat"], loc["lng"] = lat, lng
        for sub in deep_find_all(root, "subtitle"):
            if isinstance(sub, str) and re.match(r"^[\w\s.-]+,\s*[\w\s.-]+,\s*[\w\s.-]+$", sub):
                loc["address"] = sub
                break
        addr = ld.get("address") if isinstance(ld.get("address"), dict) else {}
        loc["city"] = addr.get("addressLocality") or loc["city"]
        loc["country"] = addr.get("addressCountry") or loc["country"]
        if loc["address"] and not loc["city"]:
            parts = [p.strip() for p in loc["address"].split(",")]
            if len(parts) >= 3:
                loc["city"], loc["country"] = parts[0], parts[-1]
        return loc

    def _host(self, sections, root) -> dict:
        host = {"name": None, "is_superhost": None, "response_rate": None,
                "response_time": None, "profile_url": None}
        for sec in sections.get("MEET_YOUR_HOST", []):
            card = sec.get("cardData") or {}
            host["name"] = card.get("name") or host["name"]
            host["is_superhost"] = card.get("isSuperhost", host["is_superhost"])
            if card.get("userId"):
                host["profile_url"] = f"https://www.airbnb.com/users/show/{card['userId']}"
        if not host["name"]:
            for t in deep_find_all(root, "title"):
                m = re.match(r"Hosted by (.+)", t) if isinstance(t, str) else None
                if m:
                    host["name"] = m.group(1).strip()
                    break
        for item in deep_find_all(root, "hostDetails"):
            for line in item if isinstance(item, list) else [item]:
                if isinstance(line, str):
                    m = re.search(r"Response rate:\s*(\d+%)", line)
                    if m:
                        host["response_rate"] = m.group(1)
                    m = re.search(r"Responds?\s+(within .+|in .+)", line, re.I)
                    if m:
                        host["response_time"] = m.group(1)
        return host

    def _reviews(self, root, ld) -> dict:
        agg = ld.get("aggregateRating") or {}
        rating = agg.get("ratingValue")
        count = agg.get("reviewCount") or agg.get("ratingCount")
        if rating is None:
            rating = next((v for v in deep_find_all(root, "overallRating")
                           if isinstance(v, (int, float))), None)
        if count is None:
            count = next((v for v in deep_find_all(root, "overallCount")
                          + deep_find_all(root, "reviewsCount") if isinstance(v, int)), None)
        categories = {}
        for cat in deep_find_all(root, "categoryRatings"):
            for c in cat if isinstance(cat, list) else [cat]:
                if isinstance(c, dict) and c.get("categoryType"):
                    categories[c["categoryType"].lower()] = c.get("localizedRating") or c.get("rating")
        items = []
        for rev in deep_find_all(root, "reviews"):
            for r in rev if isinstance(rev, list) else [rev]:
                if isinstance(r, dict) and r.get("comments"):
                    items.append({
                        "author": (r.get("reviewer") or {}).get("firstName"),
                        "date": r.get("localizedDate") or r.get("createdAt"),
                        "rating": r.get("rating"),
                        "text": strip_html(r.get("comments")),
                    })
        return {"rating": float(rating) if rating is not None else None,
                "count": int(count) if count is not None else None,
                "category_ratings": categories or None,
                "items": items[:20]}

    def _house_rules(self, sections, root):
        rules = {"check_in": None, "check_out": None, "max_guests": None, "other": []}
        safety, cancellation = [], None

        def classify(t):
            if not isinstance(t, str):
                return
            if re.search(r"check.?in", t, re.I) and not rules["check_in"]:
                rules["check_in"] = t
            elif re.search(r"check.?out", t, re.I) and not rules["check_out"]:
                rules["check_out"] = t
            elif re.search(r"guests? maximum", t, re.I):
                rules["max_guests"] = t
            elif t not in rules["other"]:
                rules["other"].append(t)

        for sec in sections.get("POLICIES_DEFAULT", []):
            for hr in deep_find_all(sec, "houseRules"):
                for r in hr if isinstance(hr, list) else [hr]:
                    classify(r.get("title") if isinstance(r, dict) else r)
            for hrs in deep_find_all(sec, "houseRulesSections"):
                for grp in hrs if isinstance(hrs, list) else [hrs]:
                    for item in (grp.get("items") or []) if isinstance(grp, dict) else []:
                        classify(item.get("title") if isinstance(item, dict) else None)
            for sp in (deep_find_all(sec, "safetyAndProperties")
                       + deep_find_all(sec, "safetyExpectationsAndAmenities")
                       + deep_find_all(sec, "previewSafetyAndProperties")):
                for r in sp if isinstance(sp, list) else [sp]:
                    t = r.get("title") if isinstance(r, dict) else None
                    if t and t not in safety:
                        safety.append(t)
            for cp in deep_find_all(sec, "cancellationPolicies"):
                for c in cp if isinstance(cp, list) else [cp]:
                    if isinstance(c, dict):
                        cancellation = (c.get("localized_cancellation_policy_name")
                                        or c.get("localizedCancellationPolicyName")
                                        or c.get("title") or cancellation)
        return rules, safety, cancellation

    def _pricing(self, root, page, qs) -> dict:
        pricing = {
            "currency": None,
            "check_in": (qs.get("check_in") or [None])[0],
            "check_out": (qs.get("check_out") or [None])[0],
            "display_price": None, "total": None, "breakdown": [], "note": None,
        }
        # 1) SSR JSON (usually null on Airbnb, but harmless to check)
        sdp = next((h for h in deep_find_all(root, "structuredDisplayPrice")
                    if isinstance(h, dict)), None)
        if sdp:
            primary = sdp.get("primaryLine") or {}
            pricing["display_price"] = primary.get("price") or primary.get("originalPrice")
            for line in deep_find_all(sdp, "priceBreakdown") + deep_find_all(sdp, "items"):
                for item in line if isinstance(line, list) else [line]:
                    if isinstance(item, dict) and item.get("description"):
                        pricing["breakdown"].append({
                            "label": item.get("description"),
                            "amount": item.get("priceString") or deep_find(item, "amountFormatted"),
                        })
        # 2) Rendered DOM (present when fetched with --stealth)
        if not pricing["display_price"]:
            sidebar = page.css('[data-section-id="BOOK_IT_SIDEBAR"]')
            zone = sidebar[0] if sidebar else None
            blob = text_of(zone) if zone is not None else ""
            m = re.search(MONEY_RE.pattern + r"[^.]{0,40}?(?:total|night|for \d+ nights?)",
                          blob, re.I)
            if not m:
                m = MONEY_RE.search(blob)
            if m:
                pricing["display_price"] = f"{m.group(1)}{m.group(2)}"
        if pricing["display_price"]:
            m = MONEY_RE.search(pricing["display_price"])
            if m:
                pricing["currency"] = m.group(1)
                pricing["total"] = float(m.group(2).replace(",", ""))
        else:
            pricing["note"] = ("Airbnb renders pricing client-side. Re-run with --stealth "
                               "(Scrapling's headless stealth browser) to capture live pricing.")
        return pricing


# --------------------------------------------------------------------------- #
# Booking.com parser
# --------------------------------------------------------------------------- #

class BookingParser:
    """Parses Booking.com hotel pages.

    Server-rendered data sources:
      - <script type="application/ld+json"> @type=Hotel: name, address,
        aggregateRating, description, priceRange, image
      - #hprt-table: room types, occupancy, and prices for the queried dates
      - facility lists in HTML
      - data-atlas-latlng attribute for coordinates
      - embedded gallery JSON ("large_url") for photos
    """

    def parse(self, html: str, url: str) -> dict:
        page = Selector(html)
        ld = self._ld_hotel(page)
        qs = parse_qs(urlparse(url).query)

        m = re.search(r"b_hotel_id(?:'|\")?\s*[:=]\s*'?\"?(\d+)", html)
        agg = ld.get("aggregateRating") or {}
        addr = ld.get("address") or {}

        # city sanity: some page variants put the street in addressLocality
        city = addr.get("addressLocality")
        street = addr.get("streetAddress") or ""
        if (not city or re.match(r"^\d", str(city))) and street.count(",") >= 2:
            city = [p.strip() for p in street.split(",")][-3]

        amenities = self._facilities(page)

        return {
            "source": "booking.com",
            "url": url.split("?")[0],
            "listing_id": m.group(1) if m else None,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "title": ld.get("name") or self._title(page),
            "property_type": self._property_type(page, html) or ld.get("@type"),
            "description": first_text(
                page, '[data-testid="property-description"], #property_description_content')
                or strip_html(ld.get("description")),
            "location": {
                "address": addr.get("streetAddress"),
                "city": city,
                "region": addr.get("addressRegion"),
                "postal_code": addr.get("postalCode"),
                "country": addr.get("addressCountry"),
                **self._latlng(page),
            },
            "host": {"name": self._brand(page), "type": "property_manager_or_hotel"},
            "capacity": None,  # per-room on Booking; see rooms[]
            "rooms": self._rooms(page),
            "amenities": amenities,
            "amenities_flat": flatten_amenities(amenities),
            "pricing": self._pricing(page, qs, ld),
            "reviews": self._reviews(page, agg),
            "photos": self._photos(page, html),
            "house_rules": self._rules(page, html),
        }

    # -- extraction pieces ---------------------------------------------------

    def _ld_hotel(self, page) -> dict:
        for raw in page.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") in (
                        "Hotel", "LodgingBusiness", "Apartment"):
                    return item
        return {}

    def _title(self, page) -> Optional[str]:
        t = first_text(page, '[data-testid="title"], h2.pp-header__title, #hp_hotel_name')
        if t:
            return t
        og = page.css('meta[property="og:title"]::attr(content)').get()
        return og.split(",")[0].strip() if og else None

    def _property_type(self, page, html) -> Optional[str]:
        m = re.search(r'b_hotel_type(?:\'|")?\s*[:=]\s*[\'"]([^\'"]+)', html)
        if m:
            return m.group(1)
        crumbs = page.css('[data-testid="breadcrumbs"] li')
        return text_of(crumbs[-2]) if len(crumbs) >= 2 else None

    def _brand(self, page) -> Optional[str]:
        t = first_text(page, '[data-testid="host-profile"] h3, .hp_host_name')
        return re.sub(r"^Managed by\s*", "", t) if t else None

    def _latlng(self, page) -> dict:
        el = page.css("[data-atlas-latlng]")
        if el:
            try:
                lat, lng = el[0].attrib["data-atlas-latlng"].split(",")
                return {"lat": float(lat), "lng": float(lng)}
            except (ValueError, KeyError):
                pass
        return {"lat": None, "lng": None}

    def _rooms(self, page) -> list:
        rooms, current = [], None
        for row in page.css("#hprt-table tbody > tr"):
            name_el = row.css(".hprt-roomtype-icon-link")
            if name_el:
                # facility spans carry clean English names in data-name-en;
                # meta-badges ("privacy" → Entire apartment, "room size" → 46 m²)
                # carry generic keys, so their visible text is the real value
                facs, seen_f = [], set()
                for f in row.css(".hprt-facilities-facility"):
                    attr = html_lib.unescape(f.attrib.get("data-name-en") or "").strip()
                    if not attr or attr.lower() in ("privacy", "room size"):
                        t = text_of(f)
                    else:
                        t = attr
                    if t and t not in seen_f:
                        seen_f.add(t)
                        facs.append(t)
                current = {
                    "name": text_of(name_el[0]),
                    "beds": first_text(row, ".hprt-roomtype-bed, [data-testid='room-bed-type']"),
                    "size": None,
                    "facilities": facs[:40],
                    "options": [],
                }
                for f in current["facilities"]:
                    if re.search(r"m²|sq\.? ?ft", f):
                        current["size"] = f
                        break
                rooms.append(current)
            if current is None:
                continue
            occ = row.css(".hprt-occupancy-occupancy-info, .hprt-table-cell-occupancy")
            price = row.css(".prco-valign-middle-helper, [data-testid='price-and-discounted-price']")
            conditions = [text_of(c) for c in row.css(".hprt-conditions li")][:6]
            if occ or price:
                occ_text = text_of(occ[0]) if occ else None
                if not occ_text or not re.search(r"\d", occ_text):
                    # some variants render occupancy as icons + hidden text only
                    m2 = re.search(r"Max\.?(?:imum)?\s*(?:number of\s*)?persons?:?\s*\d+",
                                   text_of(row), re.I)
                    if m2:
                        occ_text = m2.group(0)
                    else:
                        # last resort: one person-icon per guest
                        icons = row.css(".bicon-occupancy")
                        if icons:
                            occ_text = f"Max persons: {len(icons)}"
                current["options"].append({
                    "occupancy": occ_text,
                    "price": text_of(price[0]) if price else None,
                    "conditions": conditions,
                })
        return rooms

    def _facilities(self, page) -> list:
        groups = []
        for grp in page.css('[data-testid="facility-group-container"]'):
            title = first_text(grp, "h3, h2, .bui-title__text")
            items = [text_of(li) for li in grp.css("li")]
            items = [i for i in items if i]
            if items:
                groups.append({"group": title,
                               "items": [{"title": i, "available": True} for i in items]})
        if not groups:
            popular, seen = [], set()
            for li in page.css('[data-testid="property-most-popular-facilities-wrapper"] li'):
                t = text_of(li)
                if t and t not in seen:  # booking renders this block twice
                    seen.add(t)
                    popular.append(t)
            if popular:
                groups.append({"group": "Most popular facilities",
                               "items": [{"title": i, "available": True} for i in popular]})
        # aggregate every data-name-en amenity on the page (room lightboxes etc.)
        names, seen_n = [], set()
        for el in page.css("[data-name-en]"):
            n = html_lib.unescape(el.attrib.get("data-name-en", "")).strip()
            if n and n.lower() not in ("privacy", "room size") and n not in seen_n:
                seen_n.add(n)
                names.append(n)
        if names:
            groups.append({"group": "Room amenities",
                           "items": [{"title": n, "available": True} for n in names]})
        return groups

    def _pricing(self, page, qs, ld) -> dict:
        prices = []
        for el in page.css("#hprt-table .prco-valign-middle-helper, "
                           "#hprt-table [data-testid='price-and-discounted-price']"):
            m = MONEY_RE.search(text_of(el))
            if m:
                prices.append((m.group(1), float(m.group(2).replace(",", ""))))
        currency = prices[0][0] if prices else None
        check_in = (qs.get("checkin") or [None])[0]
        check_out = (qs.get("checkout") or [None])[0]
        nights = None
        if check_in and check_out:
            try:
                nights = (datetime.strptime(check_out, "%Y-%m-%d")
                          - datetime.strptime(check_in, "%Y-%m-%d")).days
            except ValueError:
                pass
        cheapest = min((p[1] for p in prices), default=None)
        return {
            "currency": currency,
            "check_in": check_in,
            "check_out": check_out,
            "nights": nights,
            "cheapest_total_for_stay": cheapest,
            "cheapest_per_night": round(cheapest / nights, 2) if cheapest and nights else None,
            "all_room_totals": [p[1] for p in prices],
            "price_range_hint": ld.get("priceRange"),
        }

    def _reviews(self, page, agg) -> dict:
        categories = {}
        for block in page.css('[data-testid="review-subscore"]'):
            m = re.match(r"(.+?)\s+(\d+(?:\.\d+)?)$", text_of(block))
            if m:
                categories[m.group(1).strip().lower()] = float(m.group(2))
        items = []
        for card in page.css('[data-testid="featuredreview"], [data-testid="review-card"], '
                             '[data-testid="featuredreview-pros-cons"]')[:20]:
            text = first_text(card, '[data-testid="featuredreview-text"], '
                                    '[data-testid="review-positive-text"]')
            author = first_text(card, '.bui-avatar-block__title, '
                                      '[data-testid="review-avatar"] + div')
            body = text or text_of(card)[:300]
            if body:
                items.append({"author": author, "text": body})
        rating = agg.get("ratingValue")
        return {"rating": float(rating) if rating is not None else None,
                "scale": 10,
                "count": agg.get("reviewCount"),
                "category_ratings": categories or None,
                "items": items}

    @staticmethod
    def _photo_id(u: str) -> str:
        """Dedupe key: the numeric photo id survives size/format URL variants."""
        m = re.search(r"/(\d+)\.(?:jpe?g|webp|png|avif)", u)
        return m.group(1) if m else u.split("?")[0]

    def _photos(self, page, html) -> list:
        urls, seen = [], set()

        def add(u, caption=None):
            # keep the query string — bstatic URLs carry a ?k= signature token
            # and return 401 without it
            u = (u or "").strip()
            pid = self._photo_id(u)
            if u and pid not in seen:
                seen.add(pid)
                urls.append({"url": u, "caption": caption or None})

        # 1) hotelPhotos JS array (raw SSR): large_url: 'https://…', alt: "…"
        for m in re.finditer(
                r"large_url:\s*'([^']+)'([\s\S]{0,300}?alt:\s*\"((?:[^\"\\]|\\.)*)\")?", html):
            cap = m.group(3)
            add(m.group(1), html_lib.unescape(cap.replace('\\"', '"')) if cap else None)
        # 2) JSON gallery form: "large_url":"https:\/\/…"
        for m in re.finditer(r'"large_url"\s*:\s*"([^"]+)"', html):
            add(m.group(1).replace("\\/", "/"))
        # 3) fallback: <img> tags, kept exactly as served (rewriting the size
        #    variant would invalidate the ?k= signature)
        for img in page.css("img[src*='bstatic.com/xdata/images/hotel']"):
            add(img.attrib.get("src", ""), img.attrib.get("alt"))
        return urls

    def _rules(self, page, html) -> dict:
        rules = {"check_in": None, "check_out": None, "other": []}
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        time_pat = (r"((?:From|Until|Before|Between)?\s*\d{1,2}:\d{2}"
                    r"(?:\s*(?:-|–|to|until)\s*\d{1,2}:\d{2})?)")
        m = re.search(r"Check-?in\s*(?:hours?)?\s*" + time_pat, text, re.I)
        if m:
            rules["check_in"] = m.group(1).strip()
        m = re.search(r"Check-?out\s*(?:hours?)?\s*" + time_pat, text, re.I)
        if m:
            rules["check_out"] = m.group(1).strip()
        return rules


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "airbnb." in host:
        return "airbnb"
    if "booking.com" in host:
        return "booking"
    raise ScraperError("UNSUPPORTED_URL", f"Not an Airbnb or Booking.com URL: {host}")


def _is_hollow(data: dict) -> bool:
    """True when a parse produced no meaningful listing data — the tell-tale of
    a bot interstitial that slipped past block detection."""
    return not (data.get("title") or data.get("photos") or data.get("rooms")
                or data.get("description"))


def scrape(url: str, use_cache: bool = True, stealth: bool = False,
           cache_dir: Optional[str] = None) -> dict:
    platform = detect_platform(url)
    fetcher = PageFetcher(
        cache_dir=cache_dir if cache_dir is not None
        else os.environ.get("SCRAPER_CACHE_DIR", ".scraper_cache"),
        cache_ttl=int(os.environ.get("SCRAPER_CACHE_TTL", "3600")),
    )
    parser = AirbnbParser() if platform == "airbnb" else BookingParser()

    def attempt(use_stealth: bool, allow_cache: bool) -> dict:
        html = fetcher.get(url, use_cache=allow_cache, stealth=use_stealth)
        try:
            data = parser.parse(html, url)
        except ScraperError:
            fetcher.invalidate(url, stealth=use_stealth)  # never keep junk pages
            raise
        if _is_hollow(data):
            fetcher.invalidate(url, stealth=use_stealth)
            raise ScraperError("EMPTY_RESULT",
                               "Fetched page contained no listing data "
                               "(likely a bot interstitial page)")
        return data

    used_stealth = stealth
    try:
        data = attempt(stealth, use_cache)
    except ScraperError as e:
        if stealth or e.code == "UNSUPPORTED_URL":
            raise
        # automatic retry with the stealth browser on a fresh fetch
        try:
            data = attempt(True, False)
            used_stealth = True
        except ScraperError as e2:
            raise ScraperError(
                e2.code, f"{e2} — retried with stealth browser and still failed; "
                         f"try again in a minute or set SCRAPER_PROXY") from e2

    # Booking A/B-serves page variants; some carry only a handful of amenity
    # badges. If the amenity yield looks thin, try the other fetch mode once
    # and keep whichever amenity set is richer.
    #
    # Skip this when the result already came from the stealth browser: that
    # path is slow (~30 s) and usually already returns the rich variant, so a
    # second stealth render rarely helps and would roughly double latency.
    if (platform == "booking" and not used_stealth
            and len(data.get("amenities_flat") or []) < 8):
        try:
            alt = attempt(not used_stealth, False)
            if len(alt.get("amenities_flat") or []) > len(data.get("amenities_flat") or []):
                data["amenities"] = alt["amenities"]
                data["amenities_flat"] = alt["amenities_flat"]
                # room facilities usually come from the same variant — take the
                # richer set there too when room names line up
                alt_rooms = {r["name"]: r for r in alt.get("rooms") or []}
                for room in data.get("rooms") or []:
                    twin = alt_rooms.get(room["name"])
                    if twin and len(twin.get("facilities") or []) > len(room.get("facilities") or []):
                        room["facilities"] = twin["facilities"]
                        room["size"] = room.get("size") or twin.get("size")
        except ScraperError:
            pass  # enrichment is best-effort
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape an Airbnb/Booking.com listing to JSON")
    ap.add_argument("url")
    ap.add_argument("--pretty", action="store_true", help="indent output")
    ap.add_argument("--output", "-o", help="write JSON to file instead of stdout")
    ap.add_argument("--no-cache", action="store_true", help="bypass the response cache")
    ap.add_argument("--stealth", action="store_true",
                    help="use Scrapling's stealth browser (JS rendering + anti-bot bypass; "
                         "enables live Airbnb pricing)")
    args = ap.parse_args()

    try:
        data = scrape(args.url, use_cache=not args.no_cache, stealth=args.stealth)
    except ScraperError as e:
        json.dump({"error": e.code, "message": str(e), "url": args.url}, sys.stdout, indent=2)
        print()
        return 1

    out = json.dumps(data, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Wrote {args.output}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
