"""Weekly Plex digest: fetch recently-added content and email a summary."""

import logging
import smtplib
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger(__name__)

# Content ratings that are NOT considered mature
_NON_MATURE_MOVIE = {"G", "PG", "PG-13"}
_NON_MATURE_TV    = {"TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14"}


def _fetch_recently_added(plex_url: str, token: str, library_id: str, days: int = 7) -> list[dict]:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    url = f"{plex_url.rstrip('/')}/library/sections/{library_id}/recentlyAdded"
    try:
        resp = httpx.get(
            url,
            params={"X-Plex-Token": token, "X-Plex-Container-Size": "500"},
            timeout=15.0,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Plex recentlyAdded failed (library %s): %s", library_id, e)
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        logger.error("Plex XML parse error: %s", e)
        return []

    items = []
    for elem in root:
        added_at = int(elem.get("addedAt") or 0)
        if added_at < cutoff:
            continue
        items.append({
            "title":             elem.get("title", ""),
            "year":              elem.get("year", ""),
            "summary":           (elem.get("summary") or "").strip()[:240],
            "rating":            elem.get("rating", ""),
            "content_rating":    elem.get("contentRating", ""),
            "genres":            [g.get("tag") for g in elem.findall("Genre") if g.get("tag")],
            "item_type":         elem.get("type", ""),
            "grandparent_title": elem.get("grandparentTitle", ""),
            "season_num":        elem.get("parentIndex", ""),
            "episode_num":       elem.get("index", ""),
            "added_at":          added_at,
        })
    return items


def fetch_digest_sections(
    plex_url: str,
    token: str,
    movie_lib_id: str,
    tv_lib_id: str,
    days: int = 7,
) -> dict[str, list]:
    """Return four content buckets: movies, mature_movies, tv, mature_tv."""
    sections: dict[str, list] = {
        "movies":        [],
        "mature_movies": [],
        "tv":            [],
        "mature_tv":     [],
    }

    if movie_lib_id:
        for item in _fetch_recently_added(plex_url, token, movie_lib_id, days):
            cr = (item["content_rating"] or "").upper().strip()
            bucket = "movies" if cr in _NON_MATURE_MOVIE else "mature_movies"
            sections[bucket].append(item)

    if tv_lib_id:
        raw_episodes = _fetch_recently_added(plex_url, token, tv_lib_id, days)
        shows: dict[str, dict] = {}
        for ep in raw_episodes:
            show_title = ep["grandparent_title"] or ep["title"]
            if show_title not in shows:
                cr = (ep["content_rating"] or "").upper().strip()
                shows[show_title] = {
                    "title":          show_title,
                    "content_rating": cr,
                    "episodes":       [],
                    "added_at":       ep["added_at"],
                }
            s_num = ep.get("season_num", "")
            e_num = ep.get("episode_num", "")
            if s_num and e_num:
                try:
                    shows[show_title]["episodes"].append(
                        f"S{int(s_num):02d}E{int(e_num):02d}"
                    )
                except (ValueError, TypeError):
                    pass

        for show in shows.values():
            cr = show["content_rating"]
            bucket = "tv" if cr in _NON_MATURE_TV else "mature_tv"
            sections[bucket].append(show)

    return sections


def render_html(sections: dict[str, list], week_label: str) -> str:
    """Render an inline-CSS HTML email from digest sections."""

    def _esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _movie_cards(items: list) -> str:
        rows = []
        for m in items:
            title   = _esc(m["title"])
            year    = _esc(str(m.get("year") or ""))
            cr      = _esc(m.get("content_rating") or "")
            rating  = m.get("rating", "")
            score   = f"{float(rating):.1f}/10" if rating else ""
            genres  = ", ".join(_esc(g) for g in (m.get("genres") or [])[:3])
            summary = _esc(m.get("summary") or "")
            meta    = " · ".join(p for p in [cr, score, genres] if p)
            rows.append(
                f'<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb">'
                f'<div style="font-size:15px;font-weight:600;color:#111827">'
                f'{title}{(" (" + year + ")") if year else ""}</div>'
                + (f'<div style="font-size:12px;color:#6b7280;margin-top:3px">{meta}</div>' if meta else "")
                + (f'<div style="font-size:13px;color:#374151;margin-top:6px;line-height:1.5">{summary}</div>' if summary else "")
                + "</td></tr>"
            )
        return "".join(rows)

    def _tv_cards(items: list) -> str:
        rows = []
        for show in items:
            title  = _esc(show["title"])
            cr     = _esc(show.get("content_rating") or "")
            eps    = sorted(set(show.get("episodes", [])))
            ep_str = ", ".join(eps[:12]) + ("…" if len(eps) > 12 else "")
            count  = f"{len(eps)} new episode{'s' if len(eps) != 1 else ''}"
            rows.append(
                f'<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb">'
                f'<div style="font-size:15px;font-weight:600;color:#111827">{title}</div>'
                f'<div style="font-size:12px;color:#6b7280;margin-top:3px">'
                + (f"{cr} · " if cr else "")
                + count
                + "</div>"
                + (f'<div style="font-size:12px;color:#9ca3af;margin-top:4px;font-family:monospace">{_esc(ep_str)}</div>' if ep_str else "")
                + "</td></tr>"
            )
        return "".join(rows)

    SECTION_DEFS = [
        ("movies",        "Movies",          _movie_cards),
        ("mature_movies", "Mature Movies",   _movie_cards),
        ("tv",            "TV Shows",        _tv_cards),
        ("mature_tv",     "Mature TV Shows", _tv_cards),
    ]

    body_parts = []
    for key, heading, renderer in SECTION_DEFS:
        items = sections.get(key, [])
        if not items:
            continue
        cards_html = renderer(items)
        body_parts.append(
            f'<tr><td style="padding-top:24px;padding-bottom:4px">'
            f'<div style="font-size:18px;font-weight:700;color:#1d4ed8;border-bottom:2px solid #1d4ed8;padding-bottom:6px">'
            f'{heading} <span style="font-size:13px;font-weight:400;color:#6b7280">({len(items)})</span>'
            f"</div></td></tr>"
            f'<tr><td><table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">'
            f"{cards_html}"
            f"</table></td></tr>"
        )

    total = sum(len(sections.get(k, [])) for k in ("movies", "mature_movies", "tv", "mature_tv"))

    if not body_parts:
        body_parts.append(
            '<tr><td style="padding:32px 0;text-align:center;color:#9ca3af;font-size:14px">'
            "No new content was added to Plex this week."
            "</td></tr>"
        )

    sections_html = "".join(body_parts)

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<title>AutoRrent Weekly Digest</title>\n"
        "</head>\n"
        '<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;background:#f9fafb">\n'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">\n'
        '<tr><td align="center" style="padding:32px 16px">\n'
        '<table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%">\n'
        '<tr><td style="background:#1d4ed8;border-radius:12px 12px 0 0;padding:24px 28px">'
        '<div style="font-size:22px;font-weight:700;color:#ffffff">AutoRrent Weekly Digest</div>'
        f'<div style="font-size:13px;color:#bfdbfe;margin-top:4px">{_esc(week_label)} &middot; '
        f"{total} new title{'s' if total != 1 else ''}</div>"
        "</td></tr>\n"
        '<tr><td style="background:#ffffff;border-radius:0 0 12px 12px;padding:0 28px 24px">'
        '<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f"{sections_html}"
        "</table></td></tr>\n"
        '<tr><td style="padding:20px 0;text-align:center;font-size:11px;color:#9ca3af">'
        "Sent by AutoRrent &middot; Manage digest settings in your AutoRrent configuration"
        "</td></tr>\n"
        "</table>\n</td></tr>\n</table>\n</body>\n</html>"
    )


def send_email(smtp_cfg: dict, recipients: list[str], html: str, week_label: str) -> None:
    """Send the digest HTML email via SMTP."""
    host      = smtp_cfg.get("host", "")
    port      = int(smtp_cfg.get("port") or 587)
    user      = smtp_cfg.get("user", "")
    password  = smtp_cfg.get("password", "")
    from_addr = smtp_cfg.get("from_email", "") or user

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"AutoRrent Weekly Digest — {week_label}"
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as server:
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=ctx)
            server.ehlo()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())

    logger.info("Weekly digest sent to %d recipient(s)", len(recipients))
