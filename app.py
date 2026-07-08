#!/usr/bin/env python3
"""
Listing Scraper Prototype — web UI + API around listing_scraper.py

Run:
    pip install -r requirements.txt
    scrapling install                    # once, for --stealth support
    uvicorn app:app --port 8000
Then open http://localhost:8000

API:
    POST /api/scrape   {"url": "...", "stealth": false, "no_cache": false}
    GET  /api/health
"""

import logging
import os
import threading
import time
from typing import Optional

from fastapi import FastAPI, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from listing_scraper import scrape, ScraperError

app = FastAPI(title="Listing Scraper Prototype")
logger = logging.getLogger("listing_scraper")

RECENT: list = []  # in-memory history of recent scrapes
_recent_lock = threading.Lock()


class ScrapeRequest(BaseModel):
    url: str
    stealth: bool = False
    no_cache: bool = False


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/scrape")
def api_scrape(req: ScrapeRequest, x_api_key: Optional[str] = Header(default=None)):
    # optional auth: set an API_KEY env var and clients must send X-API-Key
    required = os.environ.get("API_KEY")
    if required and x_api_key != required:
        return JSONResponse(status_code=401, content={
            "ok": False, "error": "UNAUTHORIZED",
            "message": "Missing or invalid X-API-Key header"})
    started = time.time()
    try:
        data = scrape(req.url.strip(), use_cache=not req.no_cache, stealth=req.stealth)
    except ScraperError as e:
        return JSONResponse(status_code=422, content={
            "ok": False, "error": e.code, "message": str(e), "url": req.url,
            "elapsed_s": round(time.time() - started, 2)})
    except Exception:  # noqa: BLE001 — don't leak internals to the client
        logger.exception("Unhandled error scraping %s", req.url)
        return JSONResponse(status_code=500, content={
            "ok": False, "error": "INTERNAL", "message": "Internal server error", "url": req.url,
            "elapsed_s": round(time.time() - started, 2)})
    elapsed = round(time.time() - started, 2)
    with _recent_lock:
        RECENT.insert(0, {"url": req.url, "title": data.get("title"),
                          "source": data.get("source"), "elapsed_s": elapsed})
        del RECENT[10:]
    return {"ok": True, "elapsed_s": elapsed, "data": data}


@app.get("/api/recent")
def recent():
    with _recent_lock:
        return list(RECENT)


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Listing Scraper — Prototype</title>
<style>
  :root { --bg:#0f1218; --panel:#171c26; --panel2:#1e2532; --text:#e8ecf3;
          --muted:#8b94a7; --accent:#ff5a5f; --accent2:#0071c2; --ok:#3fb47f;
          --err:#e05252; --border:#2a3242; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--text);
         font:15px/1.55 -apple-system,"Segoe UI",Roboto,sans-serif; padding:32px 16px; }
  .wrap { max-width:1000px; margin:0 auto; }
  h1 { font-size:22px; margin-bottom:4px; }
  .sub { color:var(--muted); margin-bottom:24px; font-size:13.5px; }
  .bar { display:flex; gap:10px; flex-wrap:wrap; }
  input[type=text] { flex:1; min-width:280px; background:var(--panel);
    border:1px solid var(--border); border-radius:10px; color:var(--text);
    padding:12px 14px; font-size:14px; outline:none; }
  input[type=text]:focus { border-color:var(--accent2); }
  button { background:var(--accent); border:0; border-radius:10px; color:#fff;
    padding:12px 22px; font-size:14px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  .opts { display:flex; gap:18px; margin:12px 2px 0; color:var(--muted); font-size:13px; }
  .opts label { display:flex; gap:6px; align-items:center; cursor:pointer; }
  .examples { margin-top:14px; display:flex; gap:8px; flex-wrap:wrap; }
  .chip { background:var(--panel2); border:1px solid var(--border); color:var(--muted);
    border-radius:20px; padding:5px 12px; font-size:12px; cursor:pointer; }
  .chip:hover { color:var(--text); border-color:var(--accent2); }
  .status { margin-top:20px; font-size:13.5px; color:var(--muted); min-height:20px; }
  .status.err { color:var(--err); }
  .status.ok { color:var(--ok); }
  .tabs { display:flex; gap:4px; margin-top:22px; }
  .tab { background:none; color:var(--muted); border:1px solid var(--border);
    border-bottom:0; border-radius:10px 10px 0 0; padding:9px 18px; font-size:13px; }
  .tab.active { background:var(--panel); color:var(--text); }
  .panel { background:var(--panel); border:1px solid var(--border);
    border-radius:0 10px 10px 10px; padding:20px; display:none; }
  .panel.active { display:block; }
  pre { white-space:pre-wrap; word-break:break-word; font:12.5px/1.5 ui-monospace,Consolas,monospace;
        color:#cdd6e4; max-height:560px; overflow:auto; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; }
  .card { background:var(--panel2); border:1px solid var(--border); border-radius:10px; padding:12px 14px; }
  .card .k { font-size:11px; letter-spacing:.6px; text-transform:uppercase; color:var(--muted); }
  .card .v { font-size:15px; margin-top:4px; }
  .section-title { margin:20px 0 10px; font-size:13px; text-transform:uppercase;
                   letter-spacing:.8px; color:var(--muted); }
  .tags { display:flex; flex-wrap:wrap; gap:6px; }
  .tag { background:var(--panel2); border:1px solid var(--border); border-radius:6px;
         padding:4px 10px; font-size:12.5px; }
  .tag.na { text-decoration:line-through; opacity:.5; }
  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); vertical-align:top; }
  th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; }
  .photos { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:10px; }
  .photos img { width:100%; height:130px; object-fit:cover; border-radius:8px;
                border:1px solid var(--border); }
  .dl { float:right; background:var(--panel2); color:var(--muted); padding:7px 14px;
        font-size:12px; font-weight:500; border:1px solid var(--border); }
  .spin { display:inline-block; width:14px; height:14px; border:2px solid var(--muted);
    border-top-color:transparent; border-radius:50%; animation:r .7s linear infinite;
    vertical-align:-2px; margin-right:7px; }
  @keyframes r { to { transform:rotate(360deg); } }
</style>
</head>
<body>
<div class="wrap">
  <h1>🏠 Listing Scraper <span style="color:var(--muted);font-weight:400">prototype</span></h1>
  <div class="sub">Paste an Airbnb or Booking.com property URL → get normalized listing JSON. Powered by Scrapling.</div>

  <div class="bar">
    <input id="url" type="text" placeholder="https://www.airbnb.com/rooms/… or https://www.booking.com/hotel/…" />
    <button id="go" onclick="run()">Scrape</button>
  </div>
  <div class="opts">
    <label><input type="checkbox" id="stealth"/> Stealth browser (JS rendering — live Airbnb pricing, anti-bot bypass; slower)</label>
    <label><input type="checkbox" id="nocache"/> Bypass cache</label>
  </div>
  <div class="examples" id="examples"></div>

  <div class="status" id="status"></div>

  <div class="tabs" id="tabs" style="display:none">
    <button class="tab active" onclick="show('summary',this)">Summary</button>
    <button class="tab" onclick="show('json',this)">Raw JSON</button>
    <button class="tab" onclick="show('photos',this)">Photos</button>
  </div>
  <div class="panel active" id="panel-summary"></div>
  <div class="panel" id="panel-json">
    <button class="dl" onclick="download()">⬇ download .json</button>
    <pre id="jsonpre"></pre>
  </div>
  <div class="panel" id="panel-photos"><div class="photos" id="photogrid"></div></div>
</div>

<script>
const EXAMPLES = [
  ["Airbnb · Noida 2BR", "https://www.airbnb.co.in/rooms/1711065697212792303?check_in=2026-07-10&check_out=2026-07-12"],
  ["Booking · London apartments", "https://www.booking.com/hotel/gb/queens-park-apartments-by-flying-butler.en-gb.html?checkin=2026-07-13&checkout=2026-07-17&group_adults=2&no_rooms=1"],
];
let LAST = null;
const API_KEY = "__API_KEY__";

document.getElementById('examples').innerHTML =
  EXAMPLES.map(e=>`<span class="chip" onclick="document.getElementById('url').value='${e[1]}'">${e[0]}</span>`).join('');
document.getElementById('url').addEventListener('keydown', e=>{ if(e.key==='Enter') run(); });

function esc(s){ return String(s??'—').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function run(){
  const url = document.getElementById('url').value.trim();
  if(!url) return;
  const btn = document.getElementById('go'), st = document.getElementById('status');
  btn.disabled = true;
  st.className = 'status';
  const stealth = document.getElementById('stealth').checked;
  st.innerHTML = `<span class="spin"></span>Scraping${stealth?' with stealth browser (can take 15–40 s)':''}…`;
  document.getElementById('tabs').style.display = 'none';
  try{
    const headers = {'Content-Type':'application/json'};
    if (API_KEY) headers['X-API-Key'] = API_KEY;
    const r = await fetch('/api/scrape', {method:'POST', headers,
      body: JSON.stringify({url, stealth, no_cache: document.getElementById('nocache').checked})});
    const j = await r.json();
    if(!j.ok){ st.className='status err'; st.textContent = `✕ ${j.error}: ${j.message} (${j.elapsed_s}s)`; return; }
    LAST = j.data;
    st.className='status ok';
    st.textContent = `✓ ${j.data.source} listing scraped in ${j.elapsed_s}s`;
    render(j.data);
  } catch(e){ st.className='status err'; st.textContent = '✕ ' + e; }
  finally { btn.disabled = false; }
}

function show(name, el){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('panel-'+name).classList.add('active');
}

function card(k,v){ return `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`; }

function render(d){
  document.getElementById('tabs').style.display = 'flex';
  document.getElementById('jsonpre').textContent = JSON.stringify(d, null, 2);
  document.getElementById('photogrid').innerHTML =
    (d.photos||[]).map(p=>`<a href="${esc(p.url)}" target="_blank"><img loading="lazy" src="${esc(p.url)}" title="${esc(p.caption)}"/></a>`).join('')
    || '<span style="color:var(--muted)">No photos extracted</span>';

  const loc = d.location||{}, rev = d.reviews||{}, pr = d.pricing||{}, cap = d.capacity||{};
  let html = `<div class="grid">`
    + card('Title', d.title)
    + card('Source', d.source)
    + card('Property type', d.property_type)
    + card('Location', [loc.city, loc.country].filter(Boolean).join(', ') || loc.address)
    + card('Rating', rev.rating!=null ? `${rev.rating}${d.source==='booking.com'?' / 10':' / 5'} · ${rev.count??'?'} reviews` : null)
    + card('Host / brand', (d.host||{}).name);
  if(d.source==='airbnb'){
    html += card('Capacity', [cap.guests&&cap.guests+' guests', cap.bedrooms&&cap.bedrooms+' BR',
                              cap.beds&&cap.beds+' beds', cap.bathrooms&&cap.bathrooms+' bath'].filter(Boolean).join(' · '));
    html += card('Price', pr.display_price || (pr.note ? 'n/a — use stealth mode' : null));
  } else {
    html += card('Dates', pr.check_in ? `${pr.check_in} → ${pr.check_out} (${pr.nights} nights)` : null);
    html += card('Cheapest option', pr.cheapest_total_for_stay!=null
      ? `${pr.currency||''}${pr.cheapest_total_for_stay.toLocaleString()} total · ${pr.currency||''}${(pr.cheapest_per_night||0).toLocaleString()}/night` : null);
  }
  html += `</div>`;

  if((d.rooms||[]).length){
    html += `<div class="section-title">Rooms (${d.rooms.length})</div><table><tr><th>Room</th><th>Beds</th><th>Size</th><th>Options</th></tr>`;
    for(const r of d.rooms){
      html += `<tr><td>${esc(r.name)}</td><td>${esc(r.beds)}</td><td>${esc(r.size)}</td><td>` +
        (r.options||[]).map(o=>`${esc(o.occupancy)} → <b>${esc(o.price)}</b>`).join('<br/>') + `</td></tr>`;
    }
    html += `</table>`;
  }

  if((d.amenities||[]).length){
    const n = d.amenities.reduce((a,g)=>a+g.items.length,0);
    html += `<div class="section-title">Amenities (${n})</div><div class="tags">`;
    for(const g of d.amenities) for(const it of g.items)
      html += `<span class="tag ${it.available===false?'na':''}" title="${esc(g.group)}">${esc(it.title)}</span>`;
    html += `</div>`;
  }

  const hr = d.house_rules||{};
  if(hr.check_in || hr.check_out){
    html += `<div class="section-title">House rules</div><div class="tags">`
      + [hr.check_in, hr.check_out, hr.max_guests, ...(hr.other||[])].filter(Boolean)
        .map(x=>`<span class="tag">${esc(x)}</span>`).join('') + `</div>`;
  }
  if(rev.category_ratings){
    html += `<div class="section-title">Review categories</div><div class="tags">`
      + Object.entries(rev.category_ratings).map(([k,v])=>`<span class="tag">${esc(k)}: <b>${esc(v)}</b></span>`).join('')
      + `</div>`;
  }
  if(d.description){
    html += `<div class="section-title">Description</div>
             <div style="color:var(--muted);font-size:13.5px;white-space:pre-wrap">${esc(d.description.slice(0,1200))}${d.description.length>1200?'…':''}</div>`;
  }
  document.getElementById('panel-summary').innerHTML = html;
}

function download(){
  if(!LAST) return;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(LAST,null,2)], {type:'application/json'}));
  a.download = `${LAST.source}_${LAST.listing_id||'listing'}.json`;
  a.click();
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    key = os.environ.get("API_KEY", "")
    return PAGE.replace("__API_KEY__", key.replace("\\", "\\\\").replace('"', '\\"'))
