"""תגיות מסודרות לשירים (כמו בספוטיפיי): שם, אמן, אלבום, מספר באלבום, שנה, סגנון ותמונת עטיפה.
המידע מגיע מהמאגר של iTunes (חינמי, בלי מפתח), קודם מהחנות הישראלית ואז מהאמריקאית.
שימוש: python tagger.py <file.mp3>  - מתייג את הקובץ במקום (לפי הכותרת והאמן שכבר בתוכו)."""
import json
import re
import sys
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

NOISE = re.compile(r"[\(\[\{][^\)\]\}]*(official|lyric|video|audio|clip|visuali[sz]er|קליפ|רשמי|מילים|live|לייב|"
                   r"hd|4k|remaster|prod\.?|music)[^\)\]\}]*[\)\]\}]", re.I)


def clean(title: str) -> str:
    t = NOISE.sub(" ", title or "")
    t = re.sub(r"#\S+", " ", t)
    t = re.sub(r"\s*[|｜]\s*.*$", "", t)  # "שם | ערוץ" -> "שם"
    t = re.sub(r"\b(ft\.?|feat\.?)\b", " ", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip(" -–—_")


def norm(s: str) -> str:
    s = re.sub(r"[^\w\s]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def itunes(term: str, country: str) -> list:
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"term": term[:120], "entity": "song", "limit": 10, "country": country})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
            return json.loads(r.read().decode()).get("results", [])
    except Exception:  # noqa: BLE001
        return []


def title_sim(cand: dict, wanted: str) -> float:
    """כמה שם השיר במאגר דומה לשם שביקשנו (בלי סוגריים, בכל צד של ' - ')."""
    t = norm(re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", cand.get("trackName", "")))
    if len(t) < 2:
        return 0.0
    parts = [wanted] + [p for p in re.split(r"\s[-–—]\s", wanted) if p.strip()]
    best = max(SequenceMatcher(None, t, norm(p)).ratio() for p in parts)
    if t in norm(wanted) and len(t) >= 4:
        best = max(best, 0.85)
    return best


def artist_ok(cand: dict, wanted: str, hint: str) -> bool:
    a = norm(cand.get("artistName", ""))
    w = norm(wanted + " " + hint)
    return bool(a) and (a in w or any(x and x in a for x in norm(hint).split(" ") if len(x) > 2))


def score(cand: dict, wanted: str, artist_hint: str) -> float:
    return title_sim(cand, wanted) + (0.2 if artist_ok(cand, wanted, artist_hint) else 0)


def lookup(title: str, artist_hint: str = "") -> dict | None:
    """מחפש את השיר ומחזיר את הפרטים המלאים, או None אם אין התאמה בטוחה (השם חייב להתאים)."""
    wanted = clean(title)
    terms = [wanted] if not artist_hint else [f"{artist_hint} {wanted}", wanted]
    best, best_s = None, 0.0
    for term in terms:
        for country in ("IL", "US"):
            for c in itunes(term, country):
                if title_sim(c, wanted) < 0.8:
                    continue
                s = score(c, wanted, artist_hint)
                if s > best_s:
                    best, best_s = c, s
            if best_s >= 1.15:
                break
        if best_s >= 1.15:
            break
    if not best:
        return None
    return {"title": best.get("trackName"), "artist": best.get("artistName"), "album": best.get("collectionName"),
            "album_artist": best.get("collectionArtistName") or best.get("artistName"),
            "track": best.get("trackNumber"), "tracks": best.get("trackCount"),
            "year": (best.get("releaseDate") or "")[:4], "genre": best.get("primaryGenreName"),
            "cover": (best.get("artworkUrl100") or "").replace("100x100bb", "600x600bb"), "score": round(best_s, 2)}


def fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
            return r.read()
    except Exception:  # noqa: BLE001
        return b""


def write_tags(path: str, meta: dict) -> None:
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, ID3NoHeaderError
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    if meta.get("title"):
        tags.setall("TIT2", [TIT2(encoding=3, text=meta["title"])])
    if meta.get("artist"):
        tags.setall("TPE1", [TPE1(encoding=3, text=meta["artist"])])
    if meta.get("album_artist"):
        tags.setall("TPE2", [TPE2(encoding=3, text=meta["album_artist"])])
    if meta.get("album"):
        tags.setall("TALB", [TALB(encoding=3, text=meta["album"])])
    if meta.get("track"):
        tags.setall("TRCK", [TRCK(encoding=3, text=f"{meta['track']}/{meta['tracks']}" if meta.get("tracks") else str(meta["track"]))])
    if meta.get("year"):
        tags.setall("TDRC", [TDRC(encoding=3, text=str(meta["year"]))])
    if meta.get("genre"):
        tags.setall("TCON", [TCON(encoding=3, text=meta["genre"])])
    img = fetch(meta["cover"]) if meta.get("cover") else b""
    if img:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img))
    tags.save(path, v2_version=3)


def current(path: str) -> tuple[str, str]:
    """הכותרת והאמן שכבר בקובץ (yt-dlp שם שם את כותרת הסרטון ואת שם הערוץ)."""
    try:
        from mutagen.id3 import ID3
        t = ID3(path)
        return str(t.get("TIT2", "")), str(t.get("TPE1", ""))
    except Exception:  # noqa: BLE001
        return "", ""


def tag_file(path: str, title: str = "", artist: str = "") -> dict | None:
    if not title:
        title, artist = current(path)
    if not title:
        title = re.sub(r"^\d+ - |\s*\[[^\]]+\]\.mp3$|\.mp3$", "", path.rsplit("/", 1)[-1])
    meta = lookup(title, re.sub(r"\s*-\s*topic$|\s+official$|vevo$", "", artist or "", flags=re.I))
    if meta:
        write_tags(path, meta)
    return meta


if __name__ == "__main__":
    for f in sys.argv[1:]:
        try:
            m = tag_file(f)
            print(f"::notice title=TAG::{'✓ ' + m['artist'] + ' - ' + m['title'] + ' | ' + str(m['album']) if m else '✗ no match'} ({f.rsplit('/', 1)[-1][:60]})", flush=True)
        except Exception as err:  # noqa: BLE001
            print(f"::warning title=TAG::{str(err)[:200]}", flush=True)
