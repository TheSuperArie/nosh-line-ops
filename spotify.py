"""קריאת שיר / אלבום / פלייליסט מספוטיפיי (בלי מפתח): דף ה-embed הציבורי מכיל את רשימת השירים."""
import json
import re
import urllib.request

SPOTIFY_URL = re.compile(r"https?://(?:open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist)/([A-Za-z0-9]{10,40})|spotify\.link/\S+)")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0 Safari/537.36", "Accept-Language": "he-IL,he;q=0.9,en;q=0.8"}


def _get(url: str) -> tuple[str, str]:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


def resolve(url: str) -> tuple[str, str] | None:
    """(סוג, מזהה) מקישור ספוטיפיי, כולל קישורי spotify.link מקוצרים."""
    m = SPOTIFY_URL.search(url)
    if m and m.group(1):
        return m.group(1), m.group(2)
    if m:
        final, page = _get(url)
        m2 = SPOTIFY_URL.search(final) or SPOTIFY_URL.search(page)
        if m2 and m2.group(1):
            return m2.group(1), m2.group(2)
    return None


def _cover(ent: dict) -> str:
    for key in ("coverArt", "visualIdentity"):
        v = ent.get(key) or {}
        srcs = v.get("sources") or v.get("image") or []
        if srcs:
            return sorted(srcs, key=lambda s: s.get("width") or s.get("maxWidth") or 0)[-1].get("url", "")
    return ""


def read(kind: str, sid: str) -> dict:
    """מחזיר {'kind','name','owner','cover','tracks':[{'title','artist','ms'}]}"""
    _, page = _get(f"https://open.spotify.com/embed/{kind}/{sid}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise RuntimeError("spotify: no page data")
    data = json.loads(m.group(1))
    ent = (((data.get("props") or {}).get("pageProps") or {}).get("state") or {}).get("data", {}).get("entity") or {}
    if not ent:
        raise RuntimeError("spotify: empty entity (keys: %s)" % list(((data.get("props") or {}).get("pageProps") or {}).keys())[:8])
    out = {"kind": kind, "name": ent.get("name") or ent.get("title") or "", "cover": _cover(ent),
           "owner": ent.get("subtitle") or ", ".join(a.get("name", "") for a in ent.get("artists") or []), "tracks": []}
    if kind == "track":
        out["tracks"].append({"title": ent.get("name") or ent.get("title") or "",
                              "artist": ", ".join(a.get("name", "") for a in ent.get("artists") or []) or ent.get("subtitle", ""),
                              "ms": ent.get("duration") or 0})
    else:
        for t in ent.get("trackList") or []:
            out["tracks"].append({"title": t.get("title") or "", "artist": t.get("subtitle") or "",
                                  "ms": t.get("duration") or 0})
    return out
