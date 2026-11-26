"""סוכן התוכניות לקו נווה שלום (שלוחה 2) - רץ בשרת במקום השעונים של GitHub (שמאחרים בשעות).
כל 5 דקות בודק אם עלתה תוכנית חדשה (הפטריוטים, מחשבות בע"מ, ינון ובן, פתחי וזמרי), ובשעות קבועות
מפעיל את המהדורות ותוכניות מוצ"ש. בכל פעם הוא מפעיל ב-GitHub את ההורדה וההעלאה לקו (workflow "line"
בפרויקט yt-agent), ושם יש בדיקה שלא מעלים פעמיים. בשבת ויום טוב לא רץ (מנהל הסוכנים עוצר אותו).
המפתח של GitHub נמצא ב-AgentCode/gh.txt (מעלה אותו workflow deploy מהסוד GH_DISPATCH)."""
import base64, html, json, os, re, subprocess, time, urllib.parse, urllib.request
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "shows_state.json")
IL = ZoneInfo("Asia/Jerusalem")
REPO = "TheSuperArie/nosh-line-ops"  # 18/11: הועבר למאגר הציבורי (בלי עלות דקות)
GAP = 25 * 60  # לא מפעילים ריצה חדשה לפני שהקודמת כנראה הסתיימה (ב-GitHub רק אחת ממתינה בתור)
UA = {"User-Agent": "Mozilla/5.0 (nosh-line shows agent)"}
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

GLZ = ("https://www.omnycontent.com/d/playlist/6dcbc33f-1fb6-49de-9ae2-ad8a00c01523/"
       "71453d31-14da-43ed-ae4a-adef00df863c/755c5b83-44d1-4a8c-b88f-adef00e16066/podcast.rss")
J = {
    "ED12": "12|המהדורה המרכזית|/2/3|מהדורה 12",
    "ED13": "13|המהדורה המרכזית|/2/3|מהדורה 13",
    "ED13S": "13|מהדורת השבת|/2/3|מהדורה 13",
    "ED11": "yt11|המהדורה המרכזית|/2/3|מהדורה 11",
    "PAT": "yt14|הפטריוטים|/2/1/3|הפטריוטים",
    "PET": "gly|פתחי וזמרי|/2/1/1|פתחי וזמרי",
    "YIN": "103|ינון מגל|/2/1/2|ינון ובן כספית",
    "GLZ": "rss|" + GLZ + "|/2/2|מחשבות בעמ",
    "OLF": "12|אולפן שישי|/2/2|אולפן שישי",
    "MEET": "12|פגוש את העיתונות|/2/2|פגוש את העיתונות",
}
# שעות קבועות (שעון ישראל). יום: 0=שני ... 5=שבת 6=ראשון (כמו weekday של פייתון)
SUN_THU = (6, 0, 1, 2, 3)
SCHEDULE = []  # 19/11: המהדורות, אולפן שישי ופגוש עברו ל-editions_agent (ישירות מהשרת, בלי GitHub ובלי דפדפן)
# טורי סוף השבוע (yt-agent, workflow "columns"): בשישי 7:00 / 9:00 / 10:00 - מייל הבחירה יוצא כשהכול עלה (ב-10 עם מה שיש)
# הפטריוטים "מחכה מוכן": ריצה ב-nosh-line-ops שמתחילה לפני שהפרק עולה ובודקת כל 20 שנ' (במקום RSS שמאחר) - כדי שהקו
# שלנו יעלה ראשון. א'-ה' + מוצ"ש (לא שישי). השומר של ה-RSS נשאר כגיבוי.
PAT_WARM = [((6, 0, 1, 2, 3, 5), "22:05", "210")]
COLUMNS = [((4,), "07:00", "7"), ((4,), "09:00", "9"), ((4,), "10:00", "10")]


def log(*a):
    print(datetime.now(IL).strftime("%d/%m %H:%M:%S"), "[shows]", *a, flush=True)


def get(url, data=None, headers=None):
    if data is None:  # קריאה רגילה דרך curl (לפייתון יש בעיה עם השם api_new של גלי ישראל)
        r = subprocess.run(["curl", "-sSL", "-m", "40", "-A", BROWSER, urllib.parse.quote(url, safe=":/?&=%#.,-_~+@")],
                           capture_output=True)
        if r.returncode:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200])
        return r.stdout.decode("utf-8", "replace")
    req = urllib.request.Request(url, data=data, headers=dict(UA, **(headers or {})))
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def ym(name, **p):
    p["token"] = KEY
    return json.loads(get(API + name, urllib.parse.urlencode(p).encode()))


def gh_token():
    q = urllib.parse.urlencode({"token": KEY, "path": "ivr2:/AgentCode/gh.txt"})
    with urllib.request.urlopen(API + "DownloadFile?" + q, timeout=60) as r:
        d = r.read()
    if d.startswith(b"B64:"):
        d = base64.b64decode(d[4:])
    t = d.decode().strip()
    if not t or t.startswith("{"):
        raise RuntimeError("אין מפתח GitHub ב-AgentCode/gh.txt")
    return t


def dispatch_event(event, payload):
    body = json.dumps({"event_type": event, "client_payload": payload}).encode()
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/dispatches", data=body, method="POST", headers={
        "Authorization": "Bearer " + gh_token(), "Accept": "application/vnd.github+json", "Content-Type": "application/json",
        "User-Agent": "nosh-line"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.status


def dispatch(jobs, repo=None, event="line", payload=None):
    body = json.dumps({"event_type": event, "client_payload": payload or {"jobs": ";".join(jobs)}}).encode()
    req = urllib.request.Request(f"https://api.github.com/repos/{repo or REPO}/dispatches", data=body, method="POST", headers={
        "Authorization": "Bearer " + gh_token(), "Accept": "application/vnd.github+json", "Content-Type": "application/json",
        "User-Agent": "nosh-line"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.status


# ---------- בדיקות מקורות: מחזירות מזהים של פרקים מלאים ----------
def c14():
    x = get("https://www.youtube.com/feeds/videos.xml?channel_id=UCKEImtWikw9usC1pl_9m1nQ")
    if "<entry>" not in x:
        raise RuntimeError("c14 rss: " + x[:120])
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
        m = re.search(r"<title>(.*?)</title>", e)
        t = html.unescape(m.group(1)) if m else ""
        vid = re.search(r"<yt:videoId>([^<]+)", e)
        if vid and "הפטריוטים" in t and "המלאה" in t:
            out.append(vid.group(1))
    return out


# ---------- סדרות מיוטיוב שיורדות במאגר הציבורי (אירוע ytline) ----------
# הרב ברוך רוזנבלום - דרשה שבועית בערוץ קהילת 'אבני החושן' (עולה בד"כ שלישי בלילה ~23:30-00:30).
# לפי הערוץ ולא לפי הפלייליסט, כי לכל חומש יש פלייליסט נפרד. נשמרות 2 אחרונות (בשלישית - הישנה נמחקת).
YTLINE = {
    "rosen": {"ch": "UCPjRHyDm5ngHYHNBn1LMc3g", "match": "רוזנבלום", "folder": "/8/2/1", "keep": 2,
              "done": "ivr2:/AgentCode/rosen_done.json", "label": "ROSENBLUM", "name": "הרב רוזנבלום",
              "plf": "רוזנבלום", "intro": "parasha"},  # רק מהפלייליסטים "הרב רוזנבלום - חומש ..."; לפני כל דרשה "פרשת ..."
    # פודקאסט לנחמיה -> 8/9/2 (נסתר). לא קבוע - בודקים את הפלייליסט פעם ביום בחצות; נמחק אחרי 30 יום (cleanup_agent)
    "nehemia": {"pl": "PLpbtu_tNqwm5f3A1xKtqnjF-qJCdwHXrd", "match": "", "folder": "/8/9/2", "keep": 30,
                "done": "ivr2:/AgentCode/nehemia_done.json", "label": "PODCAST892", "name": "פודקאסט 8/9/2",
                "hours": (0, 1), "daily": True},
    # פודקאסט פנייה מדרידיסטה לנחמיה -> 8/9/3 (26/11). רק הפלייליסט של הפרקים המלאים (בלי הקטעים הקצרים); נשמרים 4 אחרונים
    "madridista": {"pl": "PLo-Wc_h-FeEtMHoS0v47l0Z1h-S6f0D-i", "match": "", "folder": "/8/9/3", "keep": 4,
                   "done": "ivr2:/AgentCode/madridista_done.json", "label": "PODCAST893", "name": "פודקאסט 8/9/3",
                   "hours": (0, 1), "daily": True},
}


def yt_channel(cfg):
    if cfg.get("pl"):
        x = get(f"https://www.youtube.com/feeds/videos.xml?playlist_id={cfg['pl']}")
    else:
        x = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cfg['ch']}")
    if "<entry>" not in x:
        raise RuntimeError("yt rss: " + x[:120])
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
        m = re.search(r"<title>(.*?)</title>", e)
        t = html.unescape(m.group(1)) if m else ""
        vid = re.search(r"<yt:videoId>([^<]+)", e)
        if vid and all(w in t for w in cfg["match"].split("|") if w):
            out.append(vid.group(1))
    return out


def rss_full(url, need_full=True):
    out = []
    for it in re.findall(r"<item>(.*?)</item>", get(url), re.S)[:15]:
        m = re.search(r"<title>(.*?)</title>", it, re.S)
        t = html.unescape(m.group(1)).strip() if m else ""
        t = re.sub(r"^<!\[CDATA\[|\]\]>$", "", t)
        if t and (not need_full or "המלאה" in t):
            out.append(t)
    return out


def gly():
    progs = []
    for ep in ("GetProgramShowOnTopMenu", "GetPodcastShowOnTopMenu"):
        progs += json.loads(get(f"https://api_new.radiodarom.co.il/api/Program/{ep}?radioStationId=2") or "[]")
    out = []
    for p in [p for p in progs if "פתחי" in (p.get("title") or "")][:2]:
        page = get(f"https://www.gly.co.il/program?id={p['id']}")
        data = json.loads(re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S).group(1))
        for day in (data["props"]["pageProps"]["programData"].get("itemsByDate") or [])[:3]:
            for it in day.get("items") or []:
                if it.get("isFullProgram"):
                    out.append(str(it.get("itemId")))
    return out


WATCH = [  # (שם, פונקציה, משימה, שעות פעילות בשעון ישראל) - הרדיו עבר ל-radio_agent
    ("c14", c14, "PAT_OPS", (19, 9)),   # הפטריוטים: מופעל במאגר הציבורי nosh-line-ops (בלי עלות דקות)
] + [(k, (lambda c=c: yt_channel(c)), "YT:" + k, c.get("hours", (0, 24))) for k, c in YTLINE.items()]


def active(hours, now):
    a, b = hours
    return a <= now.hour < b if a < b else (now.hour >= a or now.hour < b)


def load():
    try:
        return json.load(open(STATE))
    except Exception:  # noqa: BLE001
        return {"seen": {}, "fired": {}, "pending": [], "last": 0}


def save(st):
    json.dump(st, open(STATE + ".tmp", "w"), ensure_ascii=False)
    os.replace(STATE + ".tmp", STATE)


def status(st, msg):
    """שורת מצב לתיקיית AgentCode - כדי שאפשר יהיה לראות מרחוק מה הסוכן עשה."""
    st.setdefault("log", []).append(datetime.now(IL).strftime("%d/%m %H:%M ") + msg)
    st["log"] = st["log"][-40:]
    try:
        ym("UploadTextFile", what="ivr2:/AgentCode/shows_status.txt", contents="\n".join(st["log"]))
    except Exception as e:  # noqa: BLE001
        log("status upload failed", e)


def cycle(st):
    now = datetime.now(IL)
    # 1. בדיקת מקורות
    for name, fn, job, hours in WATCH:
        first = name not in st["seen"]  # מבט ראשון (רק זוכרים מה קיים) - מיד, בלי לחכות לשעה
        if not first and not active(hours, now):
            continue
        daily = job.startswith("YT:") and YTLINE.get(job[3:], {}).get("daily")
        if daily and not first and st.setdefault("daily", {}).get(name) == str(now.date()):
            continue  # כבר נבדק היום
        try:
            ids = fn()
        except Exception as e:  # noqa: BLE001
            log("check failed", name, str(e)[:150])
            continue
        if daily:
            st.setdefault("daily", {})[name] = str(now.date())
        old = st["seen"].get(name)
        new = [i for i in ids if old is not None and i not in old]
        st["seen"][name] = list(dict.fromkeys((old or []) + ids))[-60:]
        if old is None:
            log("first look", name, len(ids), "items (not firing)")
        elif new and job == "PAT_OPS":
            for vid in new[:2]:
                try:
                    code = dispatch([], repo="TheSuperArie/nosh-line-ops", event="patriots", payload={"vid": vid})
                    status(st, f"הפטריוטים חדש {vid} - הופעל ב-nosh-line-ops ({code})")
                except Exception as e:  # noqa: BLE001
                    status(st, f"הפטריוטים {vid} - שגיאה בהפעלה: {str(e)[:100]}")
                    st["seen"][name] = [i for i in st["seen"][name] if i != vid]  # ננסה שוב בסבב הבא
        elif new and job.startswith("YT:"):
            for vid in new[:2]:  # נכנס לרשימת ההמתנה; ההפעלה (וניסיונות חוזרים) למטה
                st.setdefault("ytq", {})[vid] = {"k": job[3:], "t": 0, "n": 0}
        elif new:
            log("NEW", name, new[:3])
            if job not in st["pending"]:
                st["pending"].append(job)
            status(st, f"חדש ב-{name}: {new[0][:60]}")
    # 1ב. סדרות מיוטיוב: מפעילים, ואם עד הבדיקה הבאה (כל 3 שעות) הסרטון לא ברשימת "בוצע" - שוב (עד 8 פעמים)
    for vid, q in list(st.get("ytq", {}).items()):
        cfg = YTLINE.get(q["k"])
        if not cfg or q["n"] >= 8:
            st["ytq"].pop(vid, None)
            if cfg:
                status(st, f"{cfg['name']} {vid} - נכשל 8 פעמים, ויתרתי")
            continue
        if time.time() - q["t"] < 3 * 3600:
            continue
        if q["n"]:
            try:
                q2 = urllib.parse.urlencode({"token": KEY, "path": cfg["done"]})
                if vid in urllib.request.urlopen(API + "DownloadFile?" + q2, timeout=60).read().decode("utf-8", "replace"):
                    st["ytq"].pop(vid, None)
                    continue
            except Exception:  # noqa: BLE001
                pass
        payload = {k: str(v) for k, v in cfg.items() if k in ("folder", "keep", "done", "label", "ch", "plf", "intro")}
        payload["vid"] = vid
        q["t"], q["n"] = time.time(), q["n"] + 1
        try:
            code = dispatch([], repo="TheSuperArie/nosh-line-ops", event="ytline", payload=payload)
            status(st, f"{cfg['name']} {vid} - הופעל ב-nosh-line-ops (ניסיון {q['n']}, {code})")
        except Exception as e:  # noqa: BLE001
            status(st, f"{cfg['name']} {vid} - שגיאה בהפעלה: {str(e)[:100]}")
            q["t"] = time.time() - 3 * 3600 + 300
    # 2. שעות קבועות (חלון של 20 דקות אחרי השעה)
    for days, hm, jobs in SCHEDULE:
        h, m = map(int, hm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        k = f"{now.date()} {hm}"
        if now.weekday() in days and t <= now < t + timedelta(minutes=20) and k not in st["fired"]:
            st["fired"][k] = 1
            st["pending"] += [j for j in jobs if j not in st["pending"]]
            log("schedule", hm, jobs)
    for days, hm, arg in COLUMNS:  # טורי סוף השבוע - הפעלה נפרדת, בלי לחכות לתור של הקו
        h, m = map(int, hm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        k = f"{now.date()} cols {hm}"
        if now.weekday() in days and t <= now < t + timedelta(minutes=20) and k not in st["fired"]:
            try:
                code = dispatch_event("columns", {"step": "scan", "arg": arg})
                st["fired"][k] = 1
                log("columns dispatched", hm, code)
                status(st, f"טורי סוף השבוע {hm}: הופעל ({code})")
            except Exception as e:  # noqa: BLE001
                log("columns dispatch failed", str(e)[:200])
    for days, hm, until in PAT_WARM:
        h, m = map(int, hm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        k = f"{now.date()} patwarm"
        if now.weekday() in days and t <= now < t + timedelta(minutes=20) and k not in st["fired"]:
            try:
                code = dispatch([], repo="TheSuperArie/nosh-line-ops", event="patriots", payload={"watch": "1", "until": until})
                st["fired"][k] = 1
                status(st, f"הפטריוטים: ריצת המתנה הופעלה ({code})")
            except Exception as e:  # noqa: BLE001
                log("patwarm dispatch failed", str(e)[:200])
    st["fired"] = dict(list(st["fired"].items())[-50:])
    # 3. הפעלה ב-GitHub (מאחדים הכול לריצה אחת, ומחכים בין ריצות)
    if st["pending"] and time.time() - st.get("last", 0) > GAP:
        jobs = [J[j] for j in st["pending"]]
        try:
            code = dispatch(jobs)
            log("dispatched", st["pending"], code)
            status(st, f"הופעל ב-GitHub: {', '.join(st['pending'])} ({code})")
            st["pending"], st["last"] = [], time.time()
        except Exception as e:  # noqa: BLE001
            log("dispatch failed", str(e)[:200])
            status(st, f"שגיאה בהפעלה: {str(e)[:120]}")
            st["last"] = time.time() - GAP + 300  # ניסיון חוזר בעוד 5 דקות
    save(st)


if __name__ == "__main__":
    log("shows agent started")
    st = load()
    status(st, "הסוכן התחיל")
    save(st)
    while True:
        try:
            cycle(st)
        except Exception as e:  # noqa: BLE001
            log("cycle error", str(e)[:200])
        time.sleep(300)
