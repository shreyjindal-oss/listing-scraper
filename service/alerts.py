"""Failure-alert emails via SendGrid.

Fires when the scraper hits a real fault (blocked, fetch/parse failure,
thin/hollow content) so breakage gets noticed without someone watching logs.
Best-effort by design: a broken alert must never break a scrape request, so
every failure path here just logs and returns.

Env vars:
    SENDGRID_API_KEY   required — no-ops (with a log line) if unset
    ALERT_EMAIL_TO     default: shrey.jindal@thesqua.re
    ALERT_EMAIL_FROM   default: noreply@thesqua.re (must be a SendGrid-verified sender)
    ALERT_COOLDOWN_S   default: 900 — suppresses repeat alerts for the same
                        `key` within this window, so a broken parser hammering
                        every request doesn't flood the inbox.
"""

from __future__ import annotations

import html as html_escape
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger("listing_scraper.alerts")

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"

_last_sent: dict[str, float] = {}
_lock = threading.Lock()

_SEVERITIES = {
    "error":   {"accent": "#dc2626", "tint": "#fef2f2", "label": "Error"},
    "warning": {"accent": "#d97706", "tint": "#fffbeb", "label": "Warning"},
}


def _should_send(key: str) -> bool:
    cooldown = int(os.environ.get("ALERT_COOLDOWN_S", "900"))
    now = time.time()
    with _lock:
        last = _last_sent.get(key, 0.0)
        if now - last < cooldown:
            return False
        _last_sent[key] = now
        return True


def _render(severity: str, heading: str, fields: list[tuple[str, str]]) -> tuple[str, str]:
    """Build (plain_text, html) bodies for the same alert content."""
    style = _SEVERITIES.get(severity, _SEVERITIES["error"])
    cooldown_min = max(1, int(os.environ.get("ALERT_COOLDOWN_S", "900")) // 60)
    footnote = (f"Repeat alerts for this failure are suppressed for {cooldown_min} "
                f"more minute{'s' if cooldown_min != 1 else ''} to avoid flooding your inbox.")

    text = "\n".join([f"[{style['label'].upper()}] {heading}", ""]
                      + [f"{k}: {v}" for k, v in fields]
                      + ["", footnote])

    e = html_escape.escape
    rows = "".join(
        f'<tr>'
        f'<td style="padding:10px 16px;border-bottom:1px solid #eef0f3;color:#6b7280;'
        f'font:13px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'white-space:nowrap;vertical-align:top;">{e(k)}</td>'
        f'<td style="padding:10px 16px;border-bottom:1px solid #eef0f3;color:#111827;'
        f'font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;'
        f'word-break:break-word;">{e(str(v))}</td>'
        f'</tr>'
        for k, v in fields
    )
    html = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:24px;background:#f4f5f7;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0"
             style="max-width:560px;width:100%;background:#ffffff;border-radius:10px;
                    overflow:hidden;border:1px solid #e5e7eb;">
        <tr><td style="background:{style['accent']};height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>
        <tr>
          <td style="padding:24px 24px 16px;">
            <span style="display:inline-block;background:{style['tint']};color:{style['accent']};
                         font:700 11px/1 -apple-system,Segoe UI,Roboto,sans-serif;letter-spacing:.06em;
                         padding:4px 8px;border-radius:5px;text-transform:uppercase;">{style['label']}</span>
            <h1 style="margin:12px 0 0;font:600 18px/1.4 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
                       color:#111827;">{e(heading)}</h1>
            <p style="margin:4px 0 0;font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;
                      color:#9ca3af;">Listing Scraper</p>
          </td>
        </tr>
        <tr><td style="padding:0 24px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
        </td></tr>
        <tr>
          <td style="padding:16px 24px 24px;">
            <p style="margin:0;font:12px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
                      color:#9ca3af;">{e(footnote)}</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    return text, html


def send_alert(key: str, subject: str, severity: str, heading: str,
               fields: list[tuple[str, str]]) -> None:
    """Send an alert email, at most once per `key` per cooldown window.

    `key` identifies the *kind* of failure (e.g. "error:BLOCKED",
    "thin:booking.com") — repeats of the same kind are suppressed, but a
    different kind still gets through immediately. `severity` is "error" or
    "warning" (controls the accent color); `fields` are the label/value rows
    shown in the card, in order.
    """
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        logger.warning("SENDGRID_API_KEY not set — skipping alert: %s", subject)
        return
    if not _should_send(key):
        return

    fields = [*fields, ("Time (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))]
    text_body, html_body = _render(severity, heading, fields)

    to_addr = os.environ.get("ALERT_EMAIL_TO", "shrey.jindal@thesqua.re")
    from_addr = os.environ.get("ALERT_EMAIL_FROM", "noreply@thesqua.re")
    payload = {
        "personalizations": [{"to": [{"email": to_addr}]}],
        "from": {"email": from_addr},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html", "value": html_body},
        ],
    }
    req = urllib.request.Request(
        SENDGRID_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                logger.error("SendGrid alert failed: HTTP %s", resp.status)
    except urllib.error.HTTPError as e:
        logger.error("SendGrid alert failed: HTTP %s — %s", e.code, e.read()[:500])
    except urllib.error.URLError as e:
        logger.error("SendGrid alert failed: %s", e)
