"""טורי סוף השבוע - סריקת הכותבים, מייל בחירה (וי), בניית PDF להדפסה.
שלבים (STEP):
  scan  - שישי 7:00 / 9:00 / 10:00 (ARG = השעה). המייל נשלח רק כשכל הכותבים עלו, או ב-10:00 עם מה שיש.
          ARG=test - בדיקה: חלון של 6 ימים אחורה ושליחה מיד.
  build - אחרי "שלח" בדף הבחירה (Make): V = "week|i0,i3,...|A5|A4", S = חתימה.
  probe - בדיקת אתרים (ARG = כתובות מופרדות ב-;;)."""
import os, re, sys, json, html, time, hmac, hashlib, base64, smtplib, difflib
from datetime import datetime, timedelta, date
from email.message import EmailMessage
from urllib.parse import urljoin, quote
from zoneinfo import ZoneInfo
import requests

IL_TZ = ZoneInfo("Asia/Jerusalem")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "he-IL,he;q=0.9,en;q=0.5"}
IL_PROXY = "socks5h://127.0.0.1:1055"
STATE = "/tmp/state.json"
# מאגר ציבורי: ההגדרות רק מ-secrets. COLUMNS_CFG = שורות key=value (user / link / click), GMAIL_APP_PASSWORD לחוד
_CFG = dict(l.split("=", 1) for l in os.environ.get("COLUMNS_CFG", "").replace("\r", "").split("\n") if "=" in l)
_CFG = {k.strip(): v.strip() for k, v in _CFG.items()}
GMAIL_USER = os.environ.get("GMAIL_USER") or _CFG.get("user", "")
GMAIL_PASS = (os.environ.get("GMAIL_APP_PASSWORD") or _CFG.get("pass", "")).replace(" ", "").strip()
SECRET = os.environ.get("LINK_SECRET") or _CFG.get("link", "")
CLICK_URL = os.environ.get("MAKE_CLICK_URL") or _CFG.get("click", "")
# נמענים נוספים (כל אחד מקבל מייל בחירה משלו וקובץ משלו). secret COLUMNS_ALSO = כתובות מופרדות בפסיק/שורה
_ALSO = (os.environ.get("COLUMNS_ALSO", "") + "," + _CFG.get("also", "")).replace("\n", ",").replace(";", ",")
RECIPIENTS = [GMAIL_USER] + [a.strip() for a in _ALSO.split(",") if "@" in a and a.strip().lower() != GMAIL_USER.lower()]
_il_ok = None

# ---------- הכותבים ----------
# מקורות: ("ih", שם) ישראל היום | ("mr", מזהה) מקור ראשון | ("maariv", דף) מעריב | ("ynet", None) ידיעות (RSS) |
#          ("haaretz", None) הארץ | ("be7", שם) בשבע
WRITERS = [
    ("עמית סגל", "ישראל היום", [("ih", "עמית-סגל")]),
    ("יואב לימור", "ישראל היום", [("ih", "יואב-לימור")]),
    ("אראל סג״ל", "ישראל היום", [("ih", "אראל-סגל")]),
    ("עירית לינור", "ישראל היום", [("ih", "עירית-לינור")]),
    ("מתי טוכפלד", "מעריב", [("maariv", "mati-tuchfeld-59682"), ("ih", "מתי-טוכפלד")]),
    ("קלמן ליבסקינד", "מקור ראשון", [("mr", "76497")]),
    ("חגי סגל", "מקור ראשון", [("mr", "72478")]),
    ("בן כספית", "מעריב", [("maariv", "ben-caspit-3775")]),
    ("אלון בן דוד", "מעריב", [("maariv", "alon-ben-david-3872")]),
    ("אנה ברסקי", "מעריב", [("maariv", "anna-barsky-25513")]),
    ("אריה אלדד", "מעריב", [("maariv", "arieh-eldad-14371")]),
    ("נתן זהבי", "מעריב", [("maariv", "natan-zahavi-32")]),
    ("אבי בניהו", "מעריב", [("maariv", "avi-benayahu-9")]),
    ("נחום ברנע", "ידיעות אחרונות", [("ynet", None)]),
    ("סימה קדמון", "ידיעות אחרונות", [("ynet", None)]),
    ("בן-דרור ימיני", "ידיעות אחרונות", [("ynet", None)]),
    ("יוסי יהושע", "ידיעות אחרונות", [("ynet", None)]),
    ("יוסי ורטר", "הארץ", [("haaretz", None)]),
    ("עמנואל שילה", "בשבע", [("be7", "עמנואל-שילה")]),
    ("אבישי גרינצייג", "בשבע", [("be7", "אבישי-גרינצייג")]),
    ("יאיר יעקבי", "בשבע - אתנחתא", [("be7", "יאיר-יעקבי")]),
    ("דביר שרייבר", "בשבע - אתנחתא", [("be7", "דביר-שרייבר")]),
]
PAPER_ORDER = ["ישראל היום", "JNS", "מקור ראשון", "מעריב", "ידיעות אחרונות", "הארץ", "בשבע", "בשבע - אתנחתא"]
NEEDS_IL = ("ih", "mr")
# טורי מגזין שעולים לאתר כבר ברביעי בערב - מחפשים מרביעי בצהריים
EARLY = {"עירית לינור", "אראל סג״ל"}


# ---------- רשת ----------
def il_available() -> bool:
    global _il_ok
    if _il_ok is None:
        try:
            requests.get("https://ipinfo.io/country", proxies={"https": IL_PROXY, "http": IL_PROXY}, timeout=8)
            _il_ok = True
        except Exception:  # noqa: BLE001
            _il_ok = False
        print(f"::notice title=IL proxy::{_il_ok}", flush=True)
    return _il_ok


def fetch(url: str, il: bool = False, tries: int = 2) -> tuple[int, str]:
    """GET עם דפדפן מזויף; אם חסום/ריק מחו"ל - שוב דרך השרת בישראל."""
    last = (0, "")
    for via_il in ([True, False] if il else [False, True]):
        if via_il and not il_available():
            continue
        px = {"https": IL_PROXY, "http": IL_PROXY} if via_il else None
        for _ in range(tries):
            try:
                r = requests.get(url, headers=UA, proxies=px, timeout=25)
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding
                last = (r.status_code, r.text)
                if r.status_code == 200 and len(r.text) > 2000:
                    return last
                if r.status_code == 404:
                    return last
            except Exception as e:  # noqa: BLE001
                last = (0, f"ERR {type(e).__name__}: {e}")
            time.sleep(1)
    return last


# ---------- פענוח כתבה ----------
def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def ld_article(page: str) -> dict:
    best = {}
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip(), strict=False)
        except Exception:  # noqa: BLE001
            continue
        for d in _walk(data):
            t = d.get("@type")
            t = " ".join(t) if isinstance(t, list) else str(t or "")
            if "Article" in t and d.get("headline"):
                if len(json.dumps(d, ensure_ascii=False)) > len(json.dumps(best, ensure_ascii=False)):
                    best = d
    return best


def authors_of(ld: dict) -> str:
    a = ld.get("author")
    out = []
    for x in (a if isinstance(a, list) else [a]):
        if isinstance(x, dict):
            out.append(str(x.get("name", "")))
        elif x:
            out.append(str(x))
    return " , ".join(out)


def parse_date(s: str):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    s = re.sub(r"\.(\d{6})\d*", r".\1", s)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=IL_TZ)
    return d.astimezone(IL_TZ)


def meta(page: str, name: str) -> str:
    m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % re.escape(name), page)
    if not m:
        m = re.search(r'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="%s"' % re.escape(name), page)
    return html.unescape(m.group(1)) if m else ""


JUNK = re.compile(r"(להצטרפות|הצטרפו ל|לקבוצת ה|מצאתם טעות|טעינו\? נתקן|לכל הטורים|קראו עוד|הורידו את האפליקציה|"
                  r"תגובות לכתבה|לחצו כאן|עקבו אחרינו|ניוזלטר|כל הזכויות שמורות|^צילום:|^\(צילום)")
# תוויות של האתר שנשארו כפסקה קצרה לבד (20/11: נמצאו בקובץ של 13/11)
LABEL = re.compile(r"^(הכי מעניין|עוד כתבות בנושא|כתבות נוספות(?: בנושא)?|אולי יעניין אתכם|מומלצים|הכי נקראים|לתגובות|תגובות)\s*:?\s*\S{0,60}$")


def _norm(x: str) -> str:
    return re.sub(r"\W+", "", x)


def html_sequence(page: str, ref: str, cur: int = 0) -> list:
    """פסקאות + כותרות ביניים לפי סדר ה-HTML בתוך <article> (מעריב: הפסקה הראשונה וכותרות הביניים חסרות ב-articleBody).
    מוחזר רק אם רוב הפסקאות נמצאות גם בטקסט הייחוס (articleBody / trafilatura) - אחרת [] (לא נוגעים)."""
    m = re.search(r"<article\b.*?</article>", page, re.S)
    if not m:
        return []
    seq, started = [], False
    for tag, inner in re.findall(r"<(h[2-4]|p)\b[^>]*>(.*?)</\1>", m.group(0), re.S):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        if not t:
            continue
        if tag == "p" and len(t) > 25:
            seq.append(t)
            started = True
        elif tag in ("h3", "h4") and started and len(t) < 120:
            seq.append("§§" + t)
    ps = [x for x in seq if not x.startswith("§§")]
    rn = _norm(ref)
    hit = sum(1 for x in ps if _norm(x)[:40] in rn)
    # רק אם זה לא מאבד טקסט ביחס למה שכבר חולץ (במעריב לפעמים חלק מהגוף לא ב-<p>)
    if len(ps) >= 4 and hit >= 0.7 * len(ps) and sum(len(x) for x in ps) >= 0.97 * cur:
        while seq and seq[-1].startswith("§§"):
            seq.pop()
        return seq
    return []


def body_paragraphs(page: str, ld: dict) -> list:
    paras = []
    try:
        import trafilatura
        t = trafilatura.extract(page, include_comments=False, include_tables=False, include_images=False,
                                favor_precision=True, deduplicate=False) or ""
        paras = [p.strip() for p in t.split("\n") if p.strip()]
    except Exception as e:  # noqa: BLE001
        print("trafilatura:", e)
    ab = (ld.get("articleBody") or "").strip()
    if not ab:
        m = re.search(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', page)
        if m:
            try:
                ab = json.loads('"' + m.group(1) + '"')
            except Exception:  # noqa: BLE001
                ab = ""
    if ab and len(re.sub(r"<[^>]+>", "", ab)) > 1.03 * sum(len(p) for p in paras):  # גם פסקה אחת שחסרה
        ab = html.unescape(re.sub(r"<[^>]+>", "\n", ab)).strip()
        if "\n" in ab:
            paras = [p.strip() for p in re.split(r"\n+", ab) if p.strip()]
        else:  # גוף בלי שבירת שורות: לוקחים את הפסקאות מה-HTML של הדף
            flat = re.sub(r"\s+", " ", ab)
            hp = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", p))).strip()
                  for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)]
            hp = [p for p in hp if len(p) > 25 and p in flat]
            if sum(len(p) for p in hp) > 0.6 * len(flat):
                paras = hp
            else:
                sents = re.split(r"(?<=[.!?״\"])\s+", flat)
                paras, cur = [], ""
                for x in sents:
                    cur = (cur + " " + x).strip()
                    if len(cur) > 450:
                        paras.append(cur)
                        cur = ""
                if cur:
                    paras.append(cur)
    seq = html_sequence(page, re.sub(r"<[^>]+>", " ", ab) if ab else " ".join(paras), sum(len(x) for x in paras))
    if seq:
        paras = seq
    clean = []
    for p in paras:
        p = re.sub(r"\s+", " ", p).strip()
        if p.startswith("§§"):  # כותרת ביניים
            if not JUNK.search(p) and not LABEL.match(p[2:]):
                clean.append(p)
            continue
        if len(p) < 2 or JUNK.search(p) or (len(p) < 80 and LABEL.match(p)):
            continue
        # כפילות (פוסט מוטמע + אותו ציטוט בגוף, ציטוט מודגש שחוזר): משאירים רק את הראשון
        sents = re.split(r"(?<=[.!?])\s+", p)  # משפט שחוזר פעמיים ברצף בתוך פסקה
        p = " ".join(x for i, x in enumerate(sents) if i == 0 or len(x) < 25 or x != sents[i - 1])
        n = re.sub(r"\W+", "", p)
        # ציטוט מודגש שהופיע לפני הפסקה המלאה: מוחקים את הציטוט ומשאירים את הפסקה
        clean = clean[:-12] + [q for q in clean[-12:] if q.startswith("§§") or not (30 < len(_norm(q)) < len(n) and _norm(q) in n)]
        if any(n == re.sub(r"\W+", "", q) or (len(n) > 60 and (n in re.sub(r"\W+", "", q) or
               difflib.SequenceMatcher(None, n, re.sub(r"\W+", "", q)).ratio() > 0.9)) for q in clean[-12:]):
            continue
        clean.append(p)
    return clean


def get_article(url: str, il: bool = False) -> dict:
    st, page = fetch(url, il=il)
    if st != 200:
        return {"url": url, "err": f"http {st}"}
    ld = ld_article(page)
    title = html.unescape(ld.get("headline") or meta(page, "og:title") or "").strip()
    title = re.sub(r"\s*[|\-–]\s*(חדשות מעריב|ישראל היום|מעריב|הארץ|ynet|ידיעות אחרונות|מקור ראשון|בשבע|ערוץ 7|JNS)\s*$", "", title)
    sub = html.unescape(ld.get("description") or meta(page, "og:description") or meta(page, "description") or "").strip()
    d = parse_date(ld.get("datePublished") or meta(page, "article:published_time") or meta(page, "time") or "")
    free = ld.get("isAccessibleForFree")
    free = not (free is False or str(free).lower() == "false")
    auth = authors_of(ld) or meta(page, "author")
    paras = body_paragraphs(page, ld)
    if paras and sub:
        key = re.sub(r"[\s\[\]\.…]+", " ", sub).strip()[:60]
        first = re.sub(r"[\s\[\]\.…]+", " ", paras[0]).strip()
        if first == re.sub(r"[\s\[\]\.…]+", " ", sub).strip():
            paras = paras[1:]
        elif key and first.startswith(key):  # התקציר הוא רק תחילת הטור - לא מציגים אותו פעמיים
            sub = ""
        elif re.sub(r"\W+", "", re.sub(r"(\[\s*(\.\.\.|…)\s*\]|\.\.\.|…)\s*$", "", sub))[:40] and re.sub(r"\W+", "", paras[0]).startswith(
                re.sub(r"\W+", "", re.sub(r"(\[\s*(\.\.\.|…)\s*\]|\.\.\.|…)\s*$", "", sub))[:40]):
            sub = ""
    return {"url": url, "title": title, "sub": sub, "date": d.isoformat() if d else "", "author": auth,
            "free": free, "paras": paras, "page_has": page[:400000]}


def name_match(name: str, art: dict) -> bool:
    keys = [name, name.replace("-", " "), name.replace(" ", "-")]
    if name == "יאיר יעקבי":
        keys.append("יאיר יעקובי")
    a = art.get("author", "")
    if any(k in a for k in keys):
        return True
    # גרשיים בכל צורה (סג"ל / סג״ל / סג''ל / סגל)
    def _n(x): return re.sub(r"[\"'״׳]", "", x)
    if any(_n(k) in _n(a) for k in keys if k):
        return True
    # אין שם כותב במטא-דאטה: מחפשים בשורת הכותב בדף
    pg = art.get("page_has", "")
    return bool(not a or a in ("ערוץ 7", "בשבע", "מעריב", "ישראל היום")) and any(
        re.search(r'(?:rel="author"|writer|author|byline)[^<]{0,200}>[^<]{0,40}' + re.escape(k), pg) for k in keys)


# ---------- רשימות כתבות לפי מקור ----------
def links_for(src: str, arg) -> list:
    if src == "ih":
        st, pg = fetch(f"https://www.israelhayom.co.il/writer/{quote(arg)}", il=True)
        urls = re.findall(r'href="(/(?:[a-z0-9_-]+/)+article/\d+)"', pg)
        return [("https://www.israelhayom.co.il" + u, True) for u in urls]
    if src == "mr":
        st, pg = fetch(f"https://www.makorrishon.co.il/writer/{arg}", il=True)
        urls = re.findall(r'href="(/(?:[a-z0-9_-]+/)*article/\d+)"', pg)
        return [("https://www.makorrishon.co.il" + u, True) for u in urls]
    if src == "maariv":
        st, pg = fetch(f"https://www.maariv.co.il/journalists/{arg}")
        urls = re.findall(r'href="((?:https://www\.maariv\.co\.il)?/[a-z0-9/_-]+/article-\d+)"', pg, re.I)
        return [(urljoin("https://www.maariv.co.il/", u), False) for u in urls]
    if src == "page":
        url, rx = arg
        st, pg = fetch(url)
        return [(u, False) for u in re.findall(rx, pg)]
    if src == "be7":
        st, pg = fetch(f"https://be7.co.il/writer/{quote(arg)}/")
        urls = re.findall(r'href="(https://be7\.co\.il/\d+/)"', pg)
        return [(u, False) for u in urls]
    return []


_ynet_cache = None


def ynet_items(since: datetime) -> list:
    """ידיעות: כל הכתבות מהמוסף לשבת ומהעיתון מאז תחילת החלון (עם שם הכותב)."""
    global _ynet_cache
    if _ynet_cache is not None:
        return _ynet_cache
    seen, out = set(), []
    for feed in ("11636", "3630", "194"):
        st, x = fetch(f"https://www.ynet.co.il/Integration/StoryRss{feed}.xml")
        for it in re.findall(r"<item>(.*?)</item>", x, re.S):
            link = re.search(r"<link><!\[CDATA\[(.*?)\]\]>", it)
            pub = re.search(r"<pubDate><!\[CDATA\[(.*?)\]\]>", it)
            if not link:
                continue
            u = link.group(1).strip()
            try:
                d = datetime.strptime(pub.group(1).strip(), "%a, %d %b %Y %H:%M:%S %z") if pub else None
            except ValueError:
                d = None
            if u in seen or (d and d < since - timedelta(hours=12)):
                continue
            seen.add(u)
            out.append(u)
    print(f"::notice title=ynet::{len(out)} items since {since:%d/%m %H:%M}", flush=True)
    arts = []
    for u in out[:70]:
        a = get_article(u)
        if not a.get("err"):
            a.pop("page_has", None)
            arts.append(a)
    _ynet_cache = arts
    return arts


def haaretz_links(name: str) -> list:
    res = []
    for page_url in ("https://www.haaretz.co.il/news/politics", "https://www.haaretz.co.il/opinions", "https://www.haaretz.co.il/"):
        st, pg = fetch(page_url)
        for m in re.finditer(re.escape(name), pg):
            before = pg[max(0, m.start() - 6000):m.start()]
            hrefs = re.findall(r'href="((?:https://www\.haaretz\.co\.il)?/[^"]*ty-article[^"]*?/[0-9a-f]{8}-[0-9a-f-]{27})"', before)
            if hrefs:
                u = urljoin("https://www.haaretz.co.il/", hrefs[-1])
                if u not in res:
                    res.append(u)
        if res:
            break
    return res[:4]


def find_columns(name: str, sources: list, since: datetime, until: datetime | None = None) -> list:
    """כל הטורים של הכותב מאז תחילת החלון (החדש ראשון)."""
    found = []
    for src, arg in sources:
        if src == "ynet":
            for a in ynet_items(since):
                d = parse_date(a["date"])
                if name_match(name, a) and (not d or (d >= since and (not until or d <= until))):
                    found.append(a)
            continue
        cands = [(u, False) for u in haaretz_links(name)] if src == "haaretz" else links_for(src, arg)
        seen, old_streak = set(), 0
        for u, il in cands:
            u = u.split("?")[0].split("#")[0]
            if u in seen:
                continue
            seen.add(u)
            if len(seen) > 10 or old_streak >= 3:
                break
            a = get_article(u, il=il)
            if a.get("err") or not name_match(name, a):
                continue
            d = parse_date(a["date"])
            if d and d < since:
                old_streak += 1
                continue
            if d and until and d > until:
                continue
            a.pop("page_has", None)
            found.append(a)
        if found:
            break
    uniq, urls = [], set()
    for a in sorted(found, key=lambda a: a.get("date", ""), reverse=True):
        if a["url"] not in urls:
            urls.add(a["url"])
            uniq.append(a)
    return uniq


def status_of(a: dict) -> int:
    """1 = מוכן, 2 = בתשלום / לא נגיש."""
    words = sum(len(p.split()) for p in a.get("paras", []))
    return 1 if a.get("free", True) and words >= 150 else 2


# ---------- תאריכים ----------
def week_friday(now: datetime) -> date:
    d = now.date()
    return d + timedelta(days=(4 - d.weekday()) % 7)  # יום שישי הקרוב (או היום)


PARSHIOT = {"BERESHIS": "בראשית", "NOACH": "נח", "LECH_LECHA": "לך לך", "VAYERA": "וירא", "CHAYEI_SARA": "חיי שרה",
            "TOLDOS": "תולדות", "VAYETZEI": "ויצא", "VAYISHLACH": "וישלח", "VAYESHEV": "וישב", "MIKETZ": "מקץ",
            "VAYIGASH": "ויגש", "VAYECHI": "ויחי", "SHEMOS": "שמות", "VAERA": "וארא", "BO": "בא", "BESHALACH": "בשלח",
            "YISRO": "יתרו", "MISHPATIM": "משפטים", "TERUMAH": "תרומה", "TETZAVEH": "תצוה", "KI_SISA": "כי תשא",
            "VAYAKHEL": "ויקהל", "PEKUDEI": "פקודי", "VAYIKRA": "ויקרא", "TZAV": "צו", "SHMINI": "שמיני", "TAZRIA": "תזריע",
            "METZORA": "מצורע", "ACHREI_MOS": "אחרי מות", "KEDOSHIM": "קדושים", "EMOR": "אמור", "BEHAR": "בהר",
            "BECHUKOSAI": "בחוקותי", "BAMIDBAR": "במדבר", "NASSO": "נשא", "BEHAALOSCHA": "בהעלותך", "SHLACH": "שלח",
            "KORACH": "קרח", "CHUKAS": "חקת", "BALAK": "בלק", "PINCHAS": "פינחס", "MATOS": "מטות", "MASEI": "מסעי",
            "DEVARIM": "דברים", "VAESCHANAN": "ואתחנן", "EIKEV": "עקב", "REEH": "ראה", "SHOFTIM": "שופטים",
            "KI_SEITZEI": "כי תצא", "KI_SAVO": "כי תבוא", "NITZAVIM": "נצבים", "VAYEILECH": "וילך", "HAAZINU": "האזינו",
            "VZOS_HABERACHA": "וזאת הברכה"}
HEB_MONTHS = {1: "ניסן", 2: "אייר", 3: "סיון", 4: "תמוז", 5: "אב", 6: "אלול", 7: "תשרי", 8: "חשון", 9: "כסלו",
              10: "טבת", 11: "שבט", 12: "אדר", 13: "אדר ב'"}


def heb_num(n: int) -> str:
    vals = [(400, "ת"), (300, "ש"), (200, "ר"), (100, "ק"), (90, "צ"), (80, "פ"), (70, "ע"), (60, "ס"), (50, "נ"),
            (40, "מ"), (30, "ל"), (20, "כ"), (10, "י"), (9, "ט"), (8, "ח"), (7, "ז"), (6, "ו"), (5, "ה"), (4, "ד"),
            (3, "ג"), (2, "ב"), (1, "א")]
    out = ""
    n = n % 1000
    while n:
        if n in (15, 16):
            out += "ט" + ("ו" if n == 15 else "ז")
            break
        for v, ch in vals:
            if v <= n:
                out += ch
                n -= v
                break
    return out[:-1] + '"' + out[-1] if len(out) > 1 else out + "'"


def parsha_and_date(fri: date) -> tuple[str, str]:
    parsha, hd = "", ""
    try:
        from pyluach import dates, parshios
        sat = dates.GregorianDate(fri.year, fri.month, fri.day) + 1
        parsha = parshios.getparsha_string(sat, israel=True, hebrew=True) or ""
        hf = dates.GregorianDate(fri.year, fri.month, fri.day).to_heb()
        hd = "ערב שבת קודש, " + hf.hebrew_date_string()
    except Exception as e:  # noqa: BLE001
        print("pyluach:", type(e).__name__, e, flush=True)
    print(f"PARSHA {parsha} | {hd}", flush=True)
    return parsha, hd


# ---------- מצב ----------
def load_state() -> dict:
    try:
        return json.load(open(STATE))
    except Exception:  # noqa: BLE001
        return {}


def save_state(s: dict) -> None:
    weeks = s.setdefault("weeks", {})
    for k in sorted(weeks)[:-6]:
        weeks.pop(k)
    json.dump(s, open(STATE, "w"), ensure_ascii=False, indent=1)


def sign(vid: str, kind: str) -> str:
    return hmac.new(SECRET.encode(), f"{vid}|{kind}".encode(), hashlib.sha256).hexdigest()[:20]


# ---------- מייל ----------
def send_mail(subject: str, body_html: str, attachment: str | None = None, to: str | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = f"טורי סוף השבוע <{GMAIL_USER}>"
    msg["To"] = to or GMAIL_USER
    msg["Subject"] = subject
    msg.set_content("המייל הזה מוצג כ-HTML.")
    msg.add_alternative(f'<div dir="rtl" style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6">{body_html}</div>',
                        subtype="html")
    if attachment:
        with open(attachment, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(attachment))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.send_message(msg)
    print("mail sent:", subject, "| to #", RECIPIENTS.index(to) if to in RECIPIENTS else 0, flush=True)


def selection_mail(week: str, wk: dict) -> None:
    for r, to in enumerate(RECIPIENTS):  # לכל נמען קישור משלו; המספר שלו חתום בתוך w (בלי שינוי ב-Make)
        try:
            _selection_mail(f"{week}~{r}" if r else week, wk, to)
        except Exception as e:  # noqa: BLE001  נמען נוסף שנכשל לא יעצור את השאר
            print(f"::warning title=selection mail #{r}::{type(e).__name__}: {e}", flush=True)
            if r == 0:
                raise


def _selection_mail(w: str, wk: dict, to: str) -> None:
    week = w.split("~")[0]
    items = sorted(wk["items"], key=lambda it: (PAPER_ORDER.index(it["paper"]) if it["paper"] in PAPER_ORDER else 99))
    payload = {"w": w, "s": sign(w, "cols"), "t": wk.get("title", ""),
               "it": [[it["id"], it["writer"], it["paper"], it.get("title", ""), it["status"], 1 if it.get("default") else 0]
                      for it in items]}
    d = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    link = f"{CLICK_URL}?k=colsel&id={quote(week)}&d={d}"
    rows = []
    by = {}
    for it in items:
        by.setdefault(it["paper"], []).append(it)
    for paper in sorted(by, key=lambda p: PAPER_ORDER.index(p) if p in PAPER_ORDER else 99):
        rows.append(f'<tr><td colspan="2" style="padding:10px 0 2px;font-weight:bold;color:#7a5c2e;border-bottom:1px solid #d8c7a5">{html.escape(paper)}</td></tr>')
        for it in by[paper]:
            mark = {0: "⏳ עוד לא עלה", 1: "✅", 2: "🔒 בתשלום"}[it["status"]]
            t = html.escape(it.get("title") or "")
            rows.append(f'<tr><td style="padding:3px 0 3px 10px;white-space:nowrap">{html.escape(it["writer"])}</td>'
                        f'<td style="padding:3px 0">{mark} {t}</td></tr>')
    ready = sum(1 for it in items if it["status"] == 1)
    btn = ('display:inline-block;padding:12px 26px;border-radius:24px;background:#1a56b0;color:#fff;'
           'font-weight:bold;font-size:17px;text-decoration:none')
    body = (f'<h2 style="margin:0 0 4px">📰 טורי סוף השבוע {html.escape(wk.get("title", ""))}</h2>'
            f'<p style="margin:0 0 12px">עלו {ready} טורים. לחצו על הכפתור, סמנו ✔ את מה שרוצים ובחרו A4 או חוברת A5 - '
            f'הקובץ יגיע במייל תוך כמה דקות.</p>'
            f'<p><a href="{html.escape(link)}" style="{btn}">✔ לבחירת הטורים</a></p>'
            f'<table style="border-collapse:collapse;font-size:14px">{"".join(rows)}</table>'
            + (f'<p style="color:#8a7a62;font-size:13px">לא עלו השבוע (או בתשלום): {html.escape(", ".join(wk.get("missing", [])))}</p>'
               if wk.get("missing") else "")
            + '<p style="color:#8a7a62;font-size:13px">(בתוך מייל אי אפשר לסמן תיבות - לכן הבחירה נפתחת בדף קטן.)</p>')
    send_mail(f"📰 טורי סוף השבוע {wk.get('title', '')} - לבחירה", body, to=to)


# ---------- סריקה ----------
def do_scan(arg: str) -> None:
    now = datetime.now(IL_TZ)
    dry = arg.startswith("dry")
    test = arg.startswith("test") or dry
    fri = date.fromisoformat(arg.split(":", 1)[1]) if ":" in arg else week_friday(now)
    week = fri.isoformat()
    # רק טורי סוף השבוע: מה שעלה מחמישי (בשבע / המוסף יוצאים בחמישי) עד שישי לפני שבת
    since = datetime.combine(fri - timedelta(days=1), datetime.min.time(), IL_TZ)
    until = datetime.combine(fri, datetime.min.time(), IL_TZ).replace(hour=16)
    state = load_state()
    wk = state.setdefault("weeks", {}).setdefault(week, {})
    if wk.get("sent") and not test:
        print(f"::notice title=SCAN::week {week} already sent", flush=True)
        return
    parsha, hdate = parsha_and_date(fri)
    wk["title"] = f"פרשת {parsha}" if parsha else ""
    wk["hdate"] = hdate
    items, missing, paid = [], [], []
    for name, paper, sources in WRITERS:
        try:
            arts = find_columns(name, sources, since - timedelta(hours=36) if name in EARLY else since, until)
        except Exception as e:  # noqa: BLE001
            print(f"::warning title=WRITER {name}::{type(e).__name__}: {e}", flush=True)
            arts = []
        if not arts:
            missing.append(name)
        free_arts = [a for a in arts if status_of(a) == 1]
        if arts and not free_arts:
            paid.append(name)
        if free_arts:  # הטור העיקרי של השבוע = הארוך מבין הפתוחים
            a = max(free_arts, key=lambda a: sum(len(p.split()) for p in a["paras"]))
            items.append({"writer": name, "paper": paper, "url": a["url"], "title": a["title"], "sub": a["sub"][:300],
                          "date": a["date"], "status": 1, "default": 1, "words": sum(len(p.split()) for p in a["paras"])})
        info = "; ".join(f"{a['date'][5:16]} {a['title'][:50]} ({'free' if status_of(a) == 1 else 'PAY'}, {sum(len(p.split()) for p in a['paras'])}w)" for a in arts[:3])
        print(f"::notice title={name}::{info or 'לא נמצא'}", flush=True)
    for i, it in enumerate(items):
        it["id"] = f"i{i}"
    wk["items"] = items
    wk["missing"] = missing + paid
    wk.setdefault("checks", []).append(f"{now:%a %H:%M} missing={len(missing)}")
    hour = "test" if test else arg
    if missing and hour not in ("10", "test", "force"):
        print(f"::notice title=SCAN {hour}::missing {len(missing)}: {', '.join(missing)} - waiting", flush=True)
        save_state(state)
        return
    if dry:
        print("::notice title=DRY::" + json.dumps([[i["id"], i["writer"], i.get("title", "")[:40], i["status"]] for i in items], ensure_ascii=False)[:3800], flush=True)
    else:
        selection_mail(week, wk)
    wk["sent"] = not test
    save_state(state)


# ---------- בנייה ----------
def esc(s: str) -> str:
    return html.escape(s or "")


def fmt_date(iso: str) -> str:
    d = parse_date(iso)
    return f"{d:%d.%m.%Y}" if d else ""


CSS_COMMON = """
@font-face { font-family: Body; src: local("Frank Ruehl CLM"), local("FrankRuehlCLM-Medium"), local("Noto Serif Hebrew"), local("DejaVu Serif"); }
html { direction: rtl; }
body { font-family: "Frank Ruehl CLM", "Noto Serif Hebrew", "DejaVu Serif", serif; color: #111; margin: 0; }
h1, h2, .kicker, .byline, .toc, .cover, .runner { font-family: "Yehuda CLM", "David CLM", "Miriam CLM", "Noto Sans Hebrew", "DejaVu Sans", sans-serif; }
.cover { page: cover; text-align: center; break-after: page; }
.cover .frame { border: 3pt double #333; padding: 14mm 8mm; }
.cover .top { font-size: 11pt; letter-spacing: 2pt; color: #555; }
.cover h1 { font-size: 30pt; margin: 6mm 0 2mm; line-height: 1.15; }
.cover .parsha { font-size: 20pt; margin: 0 0 3mm; }
.cover .dates { font-size: 11pt; color: #444; }
.toc { margin-top: 9mm; text-align: right; font-size: 10.5pt; }
.toc .paper { font-weight: bold; margin: 3mm 0 1mm; border-bottom: 0.6pt solid #999; color: #333; }
.toc a { color: #111; text-decoration: none; display: block; margin: 0.8mm 0; }
.toc a::after { content: leader('.') target-counter(attr(href), page); }
.toc .w { font-weight: bold; }
article { break-before: page; }
.kicker { font-size: 9pt; color: #666; letter-spacing: 1pt; border-bottom: 0.6pt solid #999; padding-bottom: 1mm; margin-bottom: 2mm; }
h2 { font-size: 19pt; line-height: 1.15; margin: 0 0 2mm; }
.sub { font-size: 11pt; color: #333; margin: 0 0 2mm; font-weight: bold; }
.byline { font-size: 10pt; margin-bottom: 3mm; }
.byline b { font-size: 11pt; }
.hdr { break-inside: avoid; page-break-inside: avoid; break-after: avoid; page-break-after: avoid; }
.body p:first-child { break-before: avoid; }
.body h4.mid { font-family: "Yehuda CLM", "David CLM", "Noto Sans Hebrew", sans-serif; font-size: 10.5pt; font-weight: bold; margin: 2.2mm 0 0.8mm; break-after: avoid; page-break-after: avoid; }
.body p { margin: 0 0 1.2mm; text-indent: 0; text-align: justify; hyphens: none; orphans: 2; widows: 2; }

.body p:first-child { font-weight: bold; }
.end { text-align: center; margin-top: 2mm; color: #777; }
.back { page: cover; break-before: page; text-align: center; padding-top: 70mm; font-size: 24pt; font-family: "Yehuda CLM", "David CLM", sans-serif; }
.blank { break-before: page; }
"""

CSS_A5 = """
@page { size: A5; margin: 13mm 12mm 14mm 12mm;
  @bottom-center { content: counter(page); font-size: 8.5pt; font-family: "David CLM", sans-serif; }
  @top-center { content: "TITLE"; font-size: 7.5pt; color: #777; font-family: "David CLM", sans-serif; } }
@page cover { @bottom-center { content: none; } @top-center { content: none; } }
body { font-size: 10.2pt; line-height: 1.42; }
h2 { font-size: 15pt; }
.cover { padding-top: 0; }
.cover .frame { padding: 7mm 5mm; }
.cover h1 { font-size: 24pt; margin: 3mm 0 1mm; }
.cover .parsha { font-size: 16pt; }
.toc { margin-top: 5mm; font-size: 8.6pt; }
.toc .paper { margin: 1.6mm 0 0.5mm; }
.toc a { margin: 0.3mm 0; }
article { break-before: auto; margin-top: 6mm; border-top: 1pt solid #333; padding-top: 2.5mm; }
.cover + article { border-top: none; margin-top: 0; }
"""

CSS_A4 = """
@page { size: A4; margin: 15mm 14mm 16mm 14mm;
  @bottom-center { content: counter(page); font-size: 9pt; font-family: "David CLM", sans-serif; }
  @top-center { content: "TITLE"; font-size: 8pt; color: #777; font-family: "David CLM", sans-serif; } }
@page cover { @bottom-center { content: none; } @top-center { content: none; } }
body { font-size: 10.4pt; line-height: 1.4; }
article { break-before: auto; margin-top: 7mm; border-top: 1.2pt solid #333; padding-top: 3mm; }
.cover + article { border-top: none; margin-top: 0; }
.body { column-count: 2; column-gap: 7mm; column-rule: 0.4pt solid #bbb; }
.cover { padding-top: 20mm; }
"""


def build_html(arts: list, fmt: str, title: str, hdate: str, fri: date, pad: int = 0) -> str:
    head_title = f"טורי סוף השבוע · {title}" if title else "טורי סוף השבוע"
    css = CSS_COMMON + (CSS_A5 if fmt == "A5" else CSS_A4).replace("TITLE", head_title.replace('"', ""))
    toc, by = [], {}
    for a in arts:
        by.setdefault(a["paper"], []).append(a)
    for paper in sorted(by, key=lambda p: PAPER_ORDER.index(p) if p in PAPER_ORDER else 99):
        toc.append(f'<div class="paper">{esc(paper)}</div>')
        for a in by[paper]:
            toc.append(f'<a href="#{a["id"]}"><span class="w">{esc(a["writer"])}</span> – {esc(a["title"])} </a>')
    cover = (f'<section class="cover"><div class="frame"><div class="top">בס"ד</div>'
             f'<h1>טורי סוף השבוע</h1><div class="parsha">{esc(title)}</div>'
             f'<div class="dates">{esc(hdate)} · {fri:%d.%m.%Y}</div>'
             f'<div class="toc">{"".join(toc)}</div></div></section>')
    body = []
    for a in arts:
        paras = "".join(f'<h4 class="mid">{esc(p[2:])}</h4>' if p.startswith("§§") else f"<p>{esc(p)}</p>" for p in a["paras"])
        body.append(f'<article id="{a["id"]}"><div class="hdr"><div class="kicker">{esc(a["paper"])}</div><h2>{esc(a["title"])}</h2>'
                    + (f'<div class="sub">{esc(a["sub"])}</div>' if a.get("sub") else "")
                    + f'<div class="byline"><b>{esc(a["writer"])}</b> · {fmt_date(a.get("date", ""))}</div></div>'
                    f'<div class="body">{paras}</div><div class="end">❖</div></article>')
    blanks = '<div class="blank"></div>' * pad
    back = '<section class="back">שבת שלום ומבורך</section>' if fmt == "A5" else ""
    return f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><style>{css}</style></head><body>{cover}{"".join(body)}{blanks}{back}</body></html>'


def impose_booklet(src: str, dst: str) -> int:
    """חוברת: עמודי A5 משובצים שניים-שניים על A4 לרוחב, לקיפול באמצע והידוק (סדר עברי - נפתחת מימין)."""
    from pypdf import PdfReader, PdfWriter, PageObject, Transformation
    r = PdfReader(src)
    pages = list(r.pages)
    n = len(pages)
    assert n % 4 == 0, n
    w = float(pages[0].mediabox.width)
    h = float(pages[0].mediabox.height)
    out = PdfWriter()

    def sheet(left, right):
        sp = PageObject.create_blank_page(width=2 * w, height=h)
        sp.merge_transformed_page(pages[left], Transformation().translate(0, 0))
        sp.merge_transformed_page(pages[right], Transformation().translate(w, 0))
        out.add_page(sp)

    for s in range(n // 4):
        sheet(2 * s, n - 1 - 2 * s)          # צד חיצוני: שמאל = עמוד קדמי, ימין = עמוד אחורי
        sheet(n - 2 - 2 * s, 2 * s + 1)      # צד פנימי (הדפסה דו-צדדית, היפוך בצד הקצר)
    with open(dst, "wb") as f:
        out.write(f)
    return n // 4


def do_build() -> None:
    def b64(x: str) -> str:
        return base64.b64decode(x + "=" * (-len(x) % 4)).decode("utf-8", "replace") if x else ""
    v = b64(os.environ.get("V_B64", "")) or os.environ.get("ARG", "")
    if v == "nomail":
        v = os.environ.get("TESTV", "")
    sig = b64(os.environ.get("S_B64", ""))
    parts = v.split("|")
    w, ids, fmt = parts[0], [i for i in (parts[1] if len(parts) > 1 else "").split(",") if i], (parts[2] if len(parts) > 2 else "A5")
    fmt = "A4" if fmt.upper() == "A4" else "A5"
    trusted = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if not trusted and not hmac.compare_digest(sig, sign(w, "cols")):
        sys.exit("bad signature")
    week, _, r = w.partition("~")
    r = int(r) if r.isdigit() else 0
    if r >= len(RECIPIENTS):
        sys.exit(f"no recipient #{r}")
    to = RECIPIENTS[r]
    state = load_state()
    wk = state.get("weeks", {}).get(week)
    if not wk:
        sys.exit(f"no week {week}")
    fri = date.fromisoformat(week)
    since = datetime.combine(fri - timedelta(days=6), datetime.min.time(), IL_TZ)
    chosen = [it for it in wk["items"] if it["id"] in ids] or [it for it in wk["items"] if it.get("default")]
    arts, skipped = [], []
    for it in chosen:
        a = None
        if it.get("url"):
            a = get_article(it["url"], il=any(s[0] in NEEDS_IL for w in WRITERS if w[0] == it["writer"] for s in w[2]))
        else:  # לא עלה בזמן הסריקה - בודקים שוב
            srcs = next((w[2] for w in WRITERS if w[0] == it["writer"]), [])
            found = find_columns(it["writer"], srcs, since)
            a = found[0] if found else None
        if not a or a.get("err") or status_of(a) != 1:
            skipped.append(it["writer"])
            continue
        a.update({"writer": it["writer"], "paper": it["paper"], "id": f"a{len(arts)}"})
        a["title"] = a.get("title") or it.get("title", "")
        arts.append(a)
    if not arts:
        send_mail("📰 טורי סוף השבוע - לא נמצאו טורים", "<p>לא הצלחתי להוריד אף אחד מהטורים שסימנת.</p>", to=to)
        return
    order = {p: i for i, p in enumerate(PAPER_ORDER)}
    arts.sort(key=lambda a: (order.get(a["paper"], 99), [w[0] for w in WRITERS].index(a["writer"]) if a["writer"] in [w[0] for w in WRITERS] else 99))
    from weasyprint import HTML
    title, hdate = wk.get("title", ""), wk.get("hdate", "")
    if not title:
        p, hdate = parsha_and_date(fri)
        title = f"פרשת {p}" if p else ""
    os.makedirs("out", exist_ok=True)
    name = f"טורי סוף השבוע {title} {fmt}".strip().replace("  ", " ")
    final = f"out/{name}.pdf"
    if fmt == "A5":
        doc = HTML(string=build_html(arts, "A5", title, hdate, fri)).render()
        n = len(doc.pages)
        pad = (-n) % 4
        doc = HTML(string=build_html(arts, "A5", title, hdate, fri, pad)).render() if pad else doc
        doc.write_pdf("out/a5_pages.pdf")
        sheets = impose_booklet("out/a5_pages.pdf", final)
        how = (f"<p>📖 <b>חוברת A5</b>: {len(doc.pages)} עמודים על {sheets} דפי A4.</p>"
               "<p><b>איך מדפיסים:</b> הדפסה <b>דו-צדדית</b>, <b>היפוך בצד הקצר</b> (Flip on short edge), בגודל אמיתי (100%). "
               "מניחים את כל הדפים יחד, מקפלים באמצע ומהדקים בקיפול.</p>")
    else:
        try:
            doc = HTML(string=build_html(arts, "A4", title, hdate, fri)).render()
        except Exception as e:  # noqa: BLE001  באג בפריסת טורים - כל כתבה לחוד, ואם עדיין נכשל: טור אחד
            print(f"::warning title=A4 columns::{type(e).__name__} - retry", flush=True)
            try:
                doc = HTML(string=build_html(arts, "A4", title, hdate, fri).replace(".body { column-count: 2;", ".body { column-count: 2; column-fill: auto;")).render()
            except Exception:  # noqa: BLE001
                doc = HTML(string=build_html(arts, "A4", title, hdate, fri).replace("column-count: 2", "column-count: 1")).render()
        doc.write_pdf(final)
        how = (f"<p>📄 <b>A4 בשני טורים</b>: {len(doc.pages)} עמודים ממוספרים.</p>"
               "<p><b>איך מדפיסים:</b> הדפסה <b>דו-צדדית</b> (היפוך בצד הארוך), בגודל אמיתי (100%).</p>")
    lst = "".join(f"<li><b>{esc(a['writer'])}</b> ({esc(a['paper'])}) – {esc(a['title'])}</li>" for a in arts)
    skip = (f"<p>⚠️ לא נכנסו (לא עלו / בתשלום): {esc(', '.join(skipped))}</p>" if skipped else "")
    if os.environ.get("ARG") == "nomail" or os.environ.get("NOMAIL"):
        print("::notice title=BUILD::" + final, flush=True)
        return
    send_mail(f"📰 {name}", f"<h3 style='margin:0'>טורי סוף השבוע {esc(title)}</h3>{how}<ol>{lst}</ol>{skip}"
                            "<p>שבת שלום!</p>", final, to=to)
    wk.setdefault("built", []).append({"at": datetime.now(IL_TZ).isoformat()[:16], "fmt": fmt, "n": len(arts), "r": r})
    save_state(state)


# ---------- בדיקות ----------
NAMES = [w[0] for w in WRITERS]


def probe(arg: str) -> None:
    from lxml import html as LH
    for url in [u.strip() for u in arg.split(";;") if u.strip()]:
        il = url.startswith("IL:")
        url = url[3:] if il else url
        rx = ""
        if "~~" in url:
            url, rx = url.split("~~", 1)
        if url.startswith("FULL:"):  # אבחון: כל הפסקאות שיצאו מול מבנה ה-HTML של הדף
            st, page = fetch(url[5:], il=il)
            ld = ld_article(page)
            ab = ld.get("articleBody") or ""
            print(f"\n===== FULL {url[5:]} st={st} articleBody={len(ab)} nl={ab.count(chr(10))}")
            for i, p_ in enumerate(body_paragraphs(page, ld)):
                print(f"  P{i:02d} [{len(p_)}] {p_[:160]}")
            m = re.search(r"<article.*?</article>", page, re.S) or re.search(r"<body.*", page, re.S)
            blk = m.group(0) if m else page
            for t, inner in re.findall(r"<(h[1-6]|p|blockquote|strong|b)\b[^>]*>(.*?)</\1>", blk, re.S)[:400]:
                txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
                if len(txt) > 3:
                    print(f"  H<{t}> {txt[:140]}")
            continue
        if url.startswith("ART:"):
            a = get_article(url[4:], il=il)
            a.pop("page_has", None)
            print(f"\n===== ART {url[4:]}\n", json.dumps({k: (v if k != "paras" else v[:3] + [f'... {len(v)} paras']) for k, v in a.items()},
                                                       ensure_ascii=False)[:3000])
            continue
        st, txt = fetch(url, il=il)
        print(f"\n===== {url} -> {st} len={len(txt)} il={il}")
        if rx:
            for m in list(re.finditer(rx, txt, re.S))[:40]:
                print("  RX:", m.group(0)[:400].replace("\n", " "))
            continue
        try:
            doc = LH.fromstring(txt)
        except Exception as e:  # noqa: BLE001
            print("  parse err", e, txt[:300])
            continue
        print("  TITLE:", (doc.findtext(".//title") or "").strip()[:150])
        seen = set()
        for a in doc.iter("a"):
            href = a.get("href") or ""
            if not href or href in seen:
                continue
            ctx = " ".join((a.text_content() or "").split())
            hit = [n for n in NAMES if n in ctx]
            if hit or re.search(r"(writer|author|journalist)", href, re.I):
                seen.add(href)
                print(f"  A[{','.join(hit)}] {urljoin(url, href)[:200]} | {ctx[:120]}")


def main() -> None:
    step = os.environ.get("STEP", "scan")
    arg = os.environ.get("ARG", "")
    os.makedirs("out", exist_ok=True)
    if step == "check":  # רק בודק אילו ערכים חסרים ב-secrets (בלי להדפיס אותם)
        have = {"user": GMAIL_USER, "link": SECRET, "click": CLICK_URL, "GMAIL_APP_PASSWORD": GMAIL_PASS,
                "TS_OAUTH_CLIENT_ID": os.environ.get("TS_ID", ""), "COLUMNS_ALSO": ",".join(RECIPIENTS[1:])}
        for n, v in have.items():
            print(f"::notice title=CHECK {n}::{'יש' if v else 'חסר'}", flush=True)
        return
    if step != "probe" and not arg.startswith("dry"):
        miss = [n for n, v in (("user", GMAIL_USER), ("GMAIL_APP_PASSWORD", GMAIL_PASS), ("link", SECRET), ("click", CLICK_URL)) if not v]
        if miss:
            sys.exit("::error title=COLUMNS secrets::חסר ב-secrets של nosh-line-ops: " + ", ".join(miss))
    if step == "probe":
        probe(arg)
    elif step in ("scan", "retry"):
        do_scan(arg)
    elif step == "build":
        do_build()
    else:
        sys.exit(f"unknown step {step}")


if __name__ == "__main__":
    main()
