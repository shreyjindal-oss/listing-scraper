/**
 * Listing Scraper API — Cloudflare Worker
 *
 * Endpoints:
 *   GET  /api/scrape?url=<encoded listing url>&no_cache=1
 *   POST /api/scrape        {"url": "...", "no_cache": false}
 *   GET  /api/health
 *   GET  /                  usage docs (JSON)
 *
 * Auth (optional): set an API_KEY secret (`wrangler secret put API_KEY`) and
 * clients must send it as an `X-API-Key` header.
 *
 * Notes:
 *   - This is the fast-path parser (direct fetch + embedded-JSON/HTML parsing).
 *     Workers cannot run headless browsers, so JS-rendered fields (live Airbnb
 *     pricing) are not available here — the Python/Scrapling service in the
 *     parent folder covers those with stealth mode.
 *   - Responses are edge-cached for CACHE_TTL seconds (default 3600).
 */

const CACHE_TTL = 3600;

const BROWSER_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
  "Accept-Language": "en-GB,en;q=0.9",
  "Upgrade-Insecure-Requests": "1",
  "Sec-Fetch-Dest": "document",
  "Sec-Fetch-Mode": "navigate",
  "Sec-Fetch-Site": "none",
  "Sec-Fetch-User": "?1",
};

const BLOCK_MARKERS = ["captcha", "px-captcha", "are you a robot", "access denied",
  "unusual traffic", "challenge-platform", "cf-chl"];

const MONEY_RE = /([₹$€£]|USD|EUR|GBP|INR)\s?([\d,]+(?:\.\d+)?)/;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
};

// --------------------------------------------------------------------------
// helpers
// --------------------------------------------------------------------------

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS },
  });

class ScraperError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

export function deepFindAll(obj, key, maxDepth = 30) {
  const out = [];
  (function walk(o, d) {
    if (o == null || d > maxDepth) return;
    if (Array.isArray(o)) { for (const x of o) walk(x, d + 1); return; }
    if (typeof o === "object") {
      for (const k of Object.keys(o)) {
        if (k === key && o[k] != null) out.push(o[k]);
        walk(o[k], d + 1);
      }
    }
  })(obj, 0);
  return out;
}

const deepFind = (obj, key) => deepFindAll(obj, key)[0];

export function stripTags(s) {
  if (!s) return s;
  return decodeEntities(String(s).replace(/<br\s*\/?>/gi, "\n").replace(/<[^>]+>/g, ""))
    .replace(/[ \t]+/g, " ").trim();
}

function decodeEntities(s) {
  const map = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
    "&apos;": "'", "&nbsp;": " ", "&#x27;": "'", "&hellip;": "…" };
  return s.replace(/&[a-z0-9#x]+;/gi, (m) => map[m.toLowerCase()] ?? m)
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)));
}

/** All <script type="application/ld+json"> payloads in a page. */
function ldJsonBlocks(html) {
  const out = [];
  const re = /<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html))) {
    try {
      const data = JSON.parse(m[1]);
      out.push(...(Array.isArray(data) ? data : [data]));
    } catch { /* ignore malformed blocks */ }
  }
  return out;
}

const metaContent = (html, attr) => {
  const m = html.match(new RegExp(`<meta[^>]*property="${attr}"[^>]*content="([^"]*)"`, "i"))
    || html.match(new RegExp(`<meta[^>]*content="([^"]*)"[^>]*property="${attr}"`, "i"))
    || html.match(new RegExp(`<meta[^>]*name="${attr}"[^>]*content="([^"]*)"`, "i"));
  return m ? decodeEntities(m[1]) : null;
};

// --------------------------------------------------------------------------
// Airbnb parser (mirrors the Python AirbnbParser)
// --------------------------------------------------------------------------

export function parseAirbnb(html, url) {
  const stateMatch = html.match(
    /<script[^>]*id="data-deferred-state[^"]*"[^>]*>([\s\S]*?)<\/script>/i)
    || html.match(/<script[^>]*id="data-injector-instances"[^>]*>([\s\S]*?)<\/script>/i);
  if (!stateMatch)
    throw new ScraperError("PARSE_FAILED",
      "No embedded state JSON found on Airbnb page (layout changed or bot-gated)");
  const root = JSON.parse(stateMatch[1]);

  const sections = {};
  try {
    const pdp = root.niobeClientData[0][1].data.presentation.stayProductDetailPage;
    for (const s of pdp.sections.sections || [])
      (sections[s.sectionComponentType] ??= []).push(s.section || {});
  } catch { /* fall back to whole-tree deep search */ }

  const ld = ldJsonBlocks(html).find((x) =>
    ["VacationRental", "LodgingBusiness", "Product", "Place", "House"].includes(x?.["@type"])) || {};
  const qs = new URL(url).searchParams;
  const og = metaContent(html, "og:title") || "";

  // title — fallback chain, most reliable listing-name sources first:
  // TITLE_DEFAULT section → h1 → non-generic <title> → og:description (Airbnb
  // puts the listing name there; og:title holds the "Rental unit in X · ★4.7…"
  // share summary) → listingTitle in state → sharingConfig → og:title head.
  const looksLikeShareSummary = (s) => /·\s*(?:★|\d+\s*bed)/.test(s || "");
  let title = (sections.TITLE_DEFAULT || []).map((s) => s.title).find(Boolean);
  if (!title) { const h1 = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i); title = h1 ? stripTags(h1[1]) : null; }
  if (!title) {
    const t = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
    const clean = t ? stripTags(t[1]).split(" - ")[0].trim() : null;
    if (clean && !/^Airbnb[:\s]/i.test(clean)) title = clean;
  }
  if (!title) {
    const ogDesc = metaContent(html, "og:description");
    if (ogDesc && ogDesc.length <= 120 && !looksLikeShareSummary(ogDesc)) title = ogDesc.trim();
  }
  if (!title)
    title = deepFindAll(root, "listingTitle").find((t) => typeof t === "string" && !looksLikeShareSummary(t))
      ?? deepFindAll(root, "sharingConfig").find((s) => s?.title)?.title ?? null;
  if (!title && og) title = og.split("·")[0].trim();

  // capacity
  const capacity = { guests: deepFind(root, "personCapacity") ?? null,
    bedrooms: null, beds: null, bathrooms: null };
  const blob = og + " " + JSON.stringify(deepFindAll(root, "sharingConfig"));
  for (const [field, pat] of [["bedrooms", /(\d+(?:\.\d+)?)\s*bedroom/i],
    ["beds", /(\d+)\s*bed(?!room)/i],
    ["bathrooms", /(\d+(?:\.\d+)?)\s*(?:private\s+)?bath/i]]) {
    const m = blob.match(pat);
    if (m) { const v = parseFloat(m[1]); capacity[field] = Number.isInteger(v) ? v : v; }
  }

  // sleeping arrangement
  const sleeping = [];
  const seenSleep = new Set();
  for (const arr of [...deepFindAll(root, "arrangementDetails"), ...deepFindAll(root, "sleepingArrangements")])
    for (const item of Array.isArray(arr) ? arr : [arr])
      if (item && typeof item === "object" && item.title) {
        const k = item.title + "|" + item.subtitle;
        if (!seenSleep.has(k)) { seenSleep.add(k); sleeping.push({ room: item.title, beds: item.subtitle ?? null }); }
      }

  // amenities
  const amenities = [];
  for (const g of deepFind(root, "seeAllAmenitiesGroups") || deepFind(root, "previewAmenitiesGroups") || []) {
    const items = (g.amenities || []).map((a) => ({
      title: a.title,
      subtitle: (typeof a.subtitle === "object" ? a.subtitle?.text : a.subtitle) || null,
      available: a.available ?? true }));
    if (items.length) amenities.push({ group: g.title ?? null, items });
  }

  // photos
  const photos = [];
  const seenPhoto = new Set();
  for (const item of deepFindAll(root, "mediaItems"))
    for (const m of Array.isArray(item) ? item : [item])
      if (m && typeof m === "object" && m.baseUrl && !seenPhoto.has(m.baseUrl)) {
        seenPhoto.add(m.baseUrl);
        photos.push({ url: m.baseUrl, caption: stripTags(m.accessibilityLabel || "") || null });
      }

  // location
  const location = { address: null, city: null, country: null, lat: null, lng: null };
  const lat = deepFind(root, "lat"), lng = deepFind(root, "lng");
  if (typeof lat === "number") { location.lat = lat; location.lng = lng; }
  for (const sub of deepFindAll(root, "subtitle"))
    if (typeof sub === "string" && /^[\w\s.-]+,\s*[\w\s.-]+,\s*[\w\s.-]+$/.test(sub)) {
      location.address = sub; break;
    }
  const addr = typeof ld.address === "object" ? ld.address : {};
  location.city = addr.addressLocality ?? null;
  location.country = addr.addressCountry ?? null;
  if (location.address && !location.city) {
    const parts = location.address.split(",").map((p) => p.trim());
    if (parts.length >= 3) { location.city = parts[0]; location.country = parts[parts.length - 1]; }
  }

  // host
  const host = { name: null, is_superhost: null, response_rate: null, response_time: null, profile_url: null };
  for (const sec of sections.MEET_YOUR_HOST || []) {
    const card = sec.cardData || {};
    host.name = card.name ?? host.name;
    host.is_superhost = card.isSuperhost ?? host.is_superhost;
    if (card.userId) host.profile_url = `https://www.airbnb.com/users/show/${card.userId}`;
  }
  if (!host.name)
    for (const t of deepFindAll(root, "title")) {
      const m = typeof t === "string" && t.match(/^Hosted by (.+)/);
      if (m) { host.name = m[1].trim(); break; }
    }
  for (const item of deepFindAll(root, "hostDetails"))
    for (const line of Array.isArray(item) ? item : [item])
      if (typeof line === "string") {
        const r = line.match(/Response rate:\s*(\d+%)/); if (r) host.response_rate = r[1];
        const t = line.match(/Responds?\s+(within .+|in .+)/i); if (t) host.response_time = t[1];
      }

  // reviews
  const agg = ld.aggregateRating || {};
  let rating = agg.ratingValue ?? deepFindAll(root, "overallRating").find((v) => typeof v === "number") ?? null;
  let count = agg.reviewCount ?? agg.ratingCount
    ?? [...deepFindAll(root, "overallCount"), ...deepFindAll(root, "reviewsCount")]
      .find((v) => Number.isInteger(v)) ?? null;
  const categories = {};
  for (const cat of deepFindAll(root, "categoryRatings"))
    for (const c of Array.isArray(cat) ? cat : [cat])
      if (c && typeof c === "object" && c.categoryType)
        categories[c.categoryType.toLowerCase()] = c.localizedRating ?? c.rating;
  const reviewItems = [];
  for (const rev of deepFindAll(root, "reviews"))
    for (const r of Array.isArray(rev) ? rev : [rev])
      if (r && typeof r === "object" && r.comments)
        reviewItems.push({ author: r.reviewer?.firstName ?? null,
          date: r.localizedDate ?? r.createdAt ?? null,
          rating: r.rating ?? null, text: stripTags(r.comments) });

  // house rules / safety / cancellation
  const rules = { check_in: null, check_out: null, max_guests: null, other: [] };
  const safety = [];
  let cancellation = null;
  const classify = (t) => {
    if (typeof t !== "string") return;
    if (/check.?in/i.test(t) && !rules.check_in) rules.check_in = t;
    else if (/check.?out/i.test(t) && !rules.check_out) rules.check_out = t;
    else if (/guests? maximum/i.test(t)) rules.max_guests = t;
    else if (!rules.other.includes(t)) rules.other.push(t);
  };
  for (const sec of sections.POLICIES_DEFAULT || []) {
    for (const hr of deepFindAll(sec, "houseRules"))
      for (const r of Array.isArray(hr) ? hr : [hr]) classify(typeof r === "object" ? r?.title : r);
    for (const hrs of deepFindAll(sec, "houseRulesSections"))
      for (const grp of Array.isArray(hrs) ? hrs : [hrs])
        for (const item of grp?.items || []) classify(item?.title);
    for (const key of ["safetyAndProperties", "safetyExpectationsAndAmenities", "previewSafetyAndProperties"])
      for (const sp of deepFindAll(sec, key))
        for (const r of Array.isArray(sp) ? sp : [sp])
          if (r?.title && !safety.includes(r.title)) safety.push(r.title);
    for (const cp of deepFindAll(sec, "cancellationPolicies"))
      for (const c of Array.isArray(cp) ? cp : [cp])
        if (c && typeof c === "object")
          cancellation = c.localized_cancellation_policy_name
            ?? c.localizedCancellationPolicyName ?? c.title ?? cancellation;
  }

  // description
  let description = null;
  for (const key of ["htmlDescription", "descriptionOriginal"])
    for (const hit of deepFindAll(root, key)) {
      const text = typeof hit === "object" ? hit.htmlText : hit;
      if (typeof text === "string" && text.length > 40) { description = stripTags(text); break; }
    }
  if (!description) description = metaContent(html, "description");

  // pricing — SSR usually null on Airbnb (JS-rendered)
  const pricing = { currency: null, check_in: qs.get("check_in"), check_out: qs.get("check_out"),
    display_price: null, total: null, breakdown: [], note: null };
  const sdp = deepFindAll(root, "structuredDisplayPrice").find((h) => typeof h === "object");
  if (sdp) {
    const primary = sdp.primaryLine || {};
    pricing.display_price = primary.price ?? primary.originalPrice ?? null;
    for (const line of [...deepFindAll(sdp, "priceBreakdown"), ...deepFindAll(sdp, "items")])
      for (const item of Array.isArray(line) ? line : [line])
        if (item?.description)
          pricing.breakdown.push({ label: item.description,
            amount: item.priceString ?? deepFind(item, "amountFormatted") ?? null });
  }
  if (pricing.display_price) {
    const m = pricing.display_price.match(MONEY_RE);
    if (m) { pricing.currency = m[1]; pricing.total = parseFloat(m[2].replace(/,/g, "")); }
  } else {
    pricing.note = "Airbnb renders pricing client-side; not available via direct fetch. " +
      "Use the Python/Scrapling service with --stealth for live pricing.";
  }

  const idMatch = url.match(/\/rooms\/(\d+)/);
  return {
    source: "airbnb",
    url: url.split("?")[0],
    listing_id: idMatch ? idMatch[1] : null,
    scraped_at: new Date().toISOString(),
    title, property_type: ogPropertyType(og) ?? ld["@type"] ?? null,
    description, location, host, capacity,
    sleeping_arrangement: sleeping, amenities, pricing,
    reviews: { rating: rating != null ? Number(rating) : null,
      count: count != null ? Number(count) : null,
      category_ratings: Object.keys(categories).length ? categories : null,
      items: reviewItems.slice(0, 20) },
    photos,
    house_rules: rules, safety_and_property: safety, cancellation_policy: cancellation,
  };
}

const ogPropertyType = (og) => {
  const head = (og || "").split("·")[0].trim();
  return head.includes(" in ") ? head.split(" in ")[0].trim() : null;
};

// --------------------------------------------------------------------------
// Booking.com parser (mirrors the Python BookingParser)
// --------------------------------------------------------------------------

export function parseBooking(html, url) {
  const ld = ldJsonBlocks(html).find((x) =>
    ["Hotel", "LodgingBusiness", "Apartment"].includes(x?.["@type"])) || {};
  const qs = new URL(url).searchParams;
  const agg = ld.aggregateRating || {};
  const addr = ld.address || {};

  const idMatch = html.match(/b_hotel_id(?:'|")?\s*[:=]\s*'?"?(\d+)/);
  const latlng = html.match(/data-atlas-latlng="([^"]+)"/);
  let lat = null, lng = null;
  if (latlng) { const [a, b] = latlng[1].split(","); lat = parseFloat(a); lng = parseFloat(b); }

  // rooms table
  const rooms = [];
  const tableMatch = html.match(/<table[^>]*id="hprt-table"[\s\S]*?<\/table>/i);
  if (tableMatch) {
    const rows = tableMatch[0].split(/<tr[\s>]/i).slice(1);
    let current = null;
    for (const row of rows) {
      const name = row.match(/hprt-roomtype-icon-link[^>]*>([\s\S]*?)<\/a>/i);
      if (name) {
        const bed = row.match(/hprt-roomtype-bed[^>]*>([\s\S]*?)<\/div>/i);
        const facilities = [...row.matchAll(/hprt-facilities-facility[^>]*>([\s\S]*?)<\/div>/gi)]
          .map((m) => stripTags(m[1])).filter(Boolean).slice(0, 12);
        current = { name: stripTags(name[1]), beds: bed ? stripTags(bed[1]) : null,
          size: facilities.find((f) => /m²|sq\.? ?ft/.test(f)) ?? null,
          facilities, options: [] };
        rooms.push(current);
      }
      if (!current) continue;
      const occ = row.match(/Max(?:imum)?\s+persons?:?\s*\d+/i);
      const price = row.match(/prco-valign-middle-helper[^>]*>([\s\S]*?)<\/div>/i)
        || row.match(/data-testid="price-and-discounted-price"[^>]*>([\s\S]*?)<\//i);
      const conditions = [...row.matchAll(/<li[^>]*hprt-conditions[^>]*>|hprt-conditions[\s\S]*?<\/ul>/gi)];
      if (occ || price) {
        const conds = [];
        const condBlock = row.match(/hprt-conditions[\s\S]*?<\/ul>/i);
        if (condBlock)
          for (const li of condBlock[0].matchAll(/<li[^>]*>([\s\S]*?)<\/li>/gi)) {
            const t = stripTags(li[1]); if (t) conds.push(t);
          }
        current.options.push({ occupancy: occ ? occ[0] : null,
          price: price ? stripTags(price[1]) : null, conditions: conds.slice(0, 6) });
      }
    }
  }

  // pricing across all options
  const totals = [];
  let currency = null;
  for (const r of rooms) for (const o of r.options) {
    const m = (o.price || "").match(MONEY_RE);
    if (m) { currency ??= m[1]; totals.push(parseFloat(m[2].replace(/,/g, ""))); }
  }
  const checkIn = qs.get("checkin"), checkOut = qs.get("checkout");
  let nights = null;
  if (checkIn && checkOut) {
    const d = (new Date(checkOut) - new Date(checkIn)) / 86400000;
    if (Number.isFinite(d) && d > 0) nights = d;
  }
  const cheapest = totals.length ? Math.min(...totals) : null;

  // facilities
  const amenities = [];
  const popBlock = html.match(
    /property-most-popular-facilities-wrapper[\s\S]*?<\/ul>/i);
  if (popBlock) {
    const seen = new Set(); const items = [];
    for (const li of popBlock[0].matchAll(/<li[^>]*>([\s\S]*?)<\/li>/gi)) {
      const t = stripTags(li[1]);
      if (t && !seen.has(t)) { seen.add(t); items.push({ title: t, available: true }); }
    }
    if (items.length) amenities.push({ group: "Most popular facilities", items });
  }

  // review subscores — tolerate flat and nested markup by scanning a window
  const categories = {};
  for (const m of html.matchAll(/data-testid="review-subscore"[^>]*>([\s\S]{0,400}?)(?:<\/div>\s*<\/div>|<\/div>)/gi)) {
    const t = stripTags(m[1]);
    const mm = t.match(/([A-Za-z][A-Za-z &/-]*?)\s+(\d+(?:\.\d+)?)\s*$/);
    if (mm) categories[mm[1].trim().toLowerCase()] = parseFloat(mm[2]);
  }

  // photos
  const photos = [];
  const seenP = new Set();
  for (const m of html.matchAll(/"large_url"\s*:\s*"([^"]+)"/g)) {
    const u = m[1].replace(/\\\//g, "/");
    if (!seenP.has(u)) { seenP.add(u); photos.push({ url: u, caption: null }); }
  }
  if (!photos.length)
    for (const m of html.matchAll(/<img[^>]*src="(https:\/\/cf\.bstatic\.com\/xdata\/images\/hotel\/[^"?]+)[^"]*"[^>]*?(?:alt="([^"]*)")?/gi)) {
      const base = m[1].replace(/\/hotel\/[^/]+\//, "/hotel/max1024x768/");
      if (!seenP.has(base)) { seenP.add(base); photos.push({ url: base, caption: m[2] ? decodeEntities(m[2]) : null }); }
    }

  // title / type / description
  let title = ld.name ?? null;
  if (!title) {
    const t = html.match(/data-testid="title"[^>]*>([\s\S]*?)<\//i)
      || html.match(/id="hp_hotel_name"[^>]*>([\s\S]*?)<\//i);
    title = t ? stripTags(t[1]) : (metaContent(html, "og:title") || "").split(",")[0].trim() || null;
  }
  const typeMatch = html.match(/b_hotel_type(?:'|")?\s*[:=]\s*['"]([^'"]+)/);

  return {
    source: "booking.com",
    url: url.split("?")[0],
    listing_id: idMatch ? idMatch[1] : null,
    scraped_at: new Date().toISOString(),
    title,
    property_type: typeMatch ? typeMatch[1] : null,
    description: stripTags(ld.description) ?? null,
    location: { address: addr.streetAddress ?? null, city: addr.addressLocality ?? null,
      region: addr.addressRegion ?? null, postal_code: addr.postalCode ?? null,
      country: addr.addressCountry ?? null, lat, lng },
    host: { name: null, type: "property_manager_or_hotel" },
    capacity: null,
    rooms,
    amenities,
    pricing: { currency, check_in: checkIn, check_out: checkOut, nights,
      cheapest_total_for_stay: cheapest,
      cheapest_per_night: cheapest && nights ? Math.round((cheapest / nights) * 100) / 100 : null,
      all_room_totals: totals, price_range_hint: ld.priceRange ?? null },
    reviews: { rating: agg.ratingValue != null ? Number(agg.ratingValue) : null, scale: 10,
      count: agg.reviewCount ?? null,
      category_ratings: Object.keys(categories).length ? categories : null, items: [] },
    photos,
    house_rules: { check_in: null, check_out: null, other: [] },
  };
}

// --------------------------------------------------------------------------
// fetching & routing
// --------------------------------------------------------------------------

export function detectPlatform(target) {
  let host;
  try { host = new URL(target).hostname.toLowerCase(); }
  catch { throw new ScraperError("UNSUPPORTED_URL", `Not a valid URL: ${target}`); }
  if (host.includes("airbnb.")) return "airbnb";
  if (host.includes("booking.com")) return "booking";
  throw new ScraperError("UNSUPPORTED_URL", `Not an Airbnb or Booking.com URL: ${host}`);
}

async function fetchPage(target) {
  const resp = await fetch(target, { headers: BROWSER_HEADERS, redirect: "follow" });
  const html = await resp.text();
  if ([403, 429, 503].includes(resp.status))
    throw new ScraperError("BLOCKED", `HTTP ${resp.status} — the site is blocking datacenter IPs. ` +
      "Use the Python/Scrapling stealth service for this URL.");
  if (resp.status >= 400)
    throw new ScraperError("HTTP_ERROR", `HTTP ${resp.status} for ${target}`);
  const low = html.slice(0, 20000).toLowerCase();
  if (html.length < 5000 || BLOCK_MARKERS.some((m) => low.includes(m)))
    throw new ScraperError("BLOCKED", "Response looks like a bot challenge page. " +
      "Use the Python/Scrapling stealth service for this URL.");
  return html;
}

async function handleScrape(request, env, ctx) {
  // auth (only if an API_KEY secret is configured)
  if (env.API_KEY && request.headers.get("X-API-Key") !== env.API_KEY)
    return json({ ok: false, error: "UNAUTHORIZED", message: "Missing or invalid X-API-Key header" }, 401);

  let target, noCache = false;
  if (request.method === "POST") {
    let body;
    try { body = await request.json(); }
    catch { return json({ ok: false, error: "BAD_REQUEST", message: "Body must be JSON: {\"url\": \"...\"}" }, 400); }
    target = body.url; noCache = !!body.no_cache;
  } else {
    const u = new URL(request.url);
    target = u.searchParams.get("url");
    noCache = u.searchParams.get("no_cache") === "1";
  }
  if (!target) return json({ ok: false, error: "BAD_REQUEST", message: "Provide ?url=… or POST {\"url\": \"…\"}" }, 400);
  target = target.trim();

  const started = Date.now();
  try {
    const platform = detectPlatform(target);

    // edge cache
    const cache = caches.default;
    const cacheKey = new Request("https://cache.listing-scraper/" +
      encodeURIComponent(target), { method: "GET" });
    if (!noCache) {
      const hit = await cache.match(cacheKey);
      if (hit) {
        const cached = await hit.json();
        return json({ ok: true, cached: true, elapsed_s: (Date.now() - started) / 1000, data: cached });
      }
    }

    const html = await fetchPage(target);
    const data = platform === "airbnb" ? parseAirbnb(html, target) : parseBooking(html, target);

    ctx.waitUntil(cache.put(cacheKey, new Response(JSON.stringify(data), {
      headers: { "Content-Type": "application/json", "Cache-Control": `s-maxage=${CACHE_TTL}` } })));

    return json({ ok: true, cached: false, elapsed_s: (Date.now() - started) / 1000, data });
  } catch (e) {
    const code = e instanceof ScraperError ? e.code : "INTERNAL";

    // Optional stealth fallback: if a FALLBACK_URL env/secret points at the
    // Python/Scrapling service (same /api/scrape contract), forward blocked
    // requests there instead of failing.
    if (code === "BLOCKED" && env.FALLBACK_URL) {
      try {
        const fr = await fetch(env.FALLBACK_URL.replace(/\/+$/, "") + "/api/scrape", {
          method: "POST",
          headers: { "Content-Type": "application/json",
            ...(env.FALLBACK_KEY ? { "X-API-Key": env.FALLBACK_KEY } : {}) },
          body: JSON.stringify({ url: target, stealth: true }),
        });
        const fj = await fr.json();
        if (fj.ok) {
          return json({ ok: true, cached: false, via: "stealth-fallback",
            elapsed_s: (Date.now() - started) / 1000, data: fj.data });
        }
      } catch { /* fall through to the original BLOCKED error */ }
    }

    const status = code === "UNSUPPORTED_URL" || code === "BAD_REQUEST" ? 400
      : code === "BLOCKED" ? 502 : code === "INTERNAL" ? 500 : 422;
    return json({ ok: false, error: code, message: e.message, url: target,
      elapsed_s: (Date.now() - started) / 1000 }, status);
  }
}

const USAGE = {
  service: "listing-scraper-api",
  usage: {
    "GET /api/scrape?url=<encoded listing url>": "scrape a listing (add &no_cache=1 to bypass cache)",
    "POST /api/scrape": '{"url": "https://…", "no_cache": false}',
    "GET /api/health": "health check",
  },
  auth: "if the worker has an API_KEY secret set, send it as an X-API-Key header",
  supported: ["airbnb.*/rooms/<id>", "booking.com/hotel/…"],
  notes: [
    "Airbnb pricing is JS-rendered and not available via direct fetch — use the Scrapling stealth service for that field.",
    "Responses are edge-cached for 1 hour; pass no_cache to force a refresh.",
  ],
};

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const { pathname } = new URL(request.url);
    if (pathname === "/api/health") return json({ status: "ok" });
    if (pathname === "/api/scrape") return handleScrape(request, env, ctx);
    if (pathname === "/api/docs") return json(USAGE);
    if (pathname === "/") return json(USAGE);  // fallback when assets aren't deployed
    return json({ ok: false, error: "NOT_FOUND", message: `No route ${pathname}` }, 404);
  },
};
