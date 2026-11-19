"""המהדורות לקו נווה שלום - ישירות מהשרת בישראל, בלי GitHub, בלי Tailscale ובלי דפדפן (הדרך שקציר מצא, 19/11).
13: מערכת הווידאו של 13 (Kaltura OTT, partner 5031) נותנת לכל פרק entryId -> הזרם ב-Kaltura הרגיל (partner 2748741)
    -> ffmpeg מוריד ישירות ל-MP3 קטן (בלי פרסומות).
12: דף הפרק במאקו נקרא עם curl_cffi בהתחזות לכרום (Radware חוסם curl רגיל) -> הנתיב /i/....csmil/master.m3u8
    -> כרטיס מ-entitlementsServicesV2.jsp -> ffmpeg מוריד מ-mako-vod.akamaized.net.
    Radware סופר את כל הפניות מכתובת השרת (גם של קציר) - לכן לפחות דקה בין פנייה לפנייה למאקו.
כל משימה בודקת בחלון שעות של ערב/לילה אם יש פרק של הערב הזה, מעלה פעם אחת ונחה עד הערב הבא.
מצב: ivr2:/AgentCode/editions_status.txt  |  בדיקה עצמית בהפעלה הראשונה (60 שניות מכל ערוץ -> AgentCode/test_ed12/13)."""
import base64, html, json, os, re, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(DIR, "bin")
PYLIB = os.path.join(DIR, "pylib")
STATE = os.path.join(DIR, "editions_state.json")
TMP = os.path.join(DIR, "editions_tmp.mp3")
IL = ZoneInfo("Asia/Jerusalem")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
SELFTEST = "v4"
SELFTEST_CH = ("13",)  # 12 כבר עבר בבדיקה (19/11) - לא מעמיסים על מאקו  # להעלות כדי להריץ שוב את הבדיקה העצמית

# יום: 0=שני ... 4=שישי 5=שבת 6=ראשון. החלון מתחיל בשעה h1 ביום שברשימה ונמשך עד h2 בבוקר שאחריו.
SUN_THU, SAT = (6, 0, 1, 2, 3), (5,)
JOBS = [  # (מזהה, ערוץ, מה לחפש, ימים, (h1, h2), תיקייה, כמה לשמור, תווית)
    ("ED12", "12", "https://www.mako.co.il/mako-vod-channel2-news/main-newscast", SUN_THU + SAT, (21, 3), "/2/3", 15, "מהדורה 12"),
    ("ED13", "13", "המהדורה המרכזית", SUN_THU, (21, 3), "/2/3", 15, "מהדורה 13"),
    ("ED13S", "13", "מהדורת השבת", SAT, (20, 3), "/2/3", 15, "מהדורה 13"),
    ("DRUCKER", "13", "@drucker", SUN_THU, (20, 3), "/2/2", 15, "רביב דרוקר"),
    ("OLF", "12", "אולפן שישי", SAT, (18, 3), "/2/2", 15, "אולפן שישי"),
    ("MEET", "12", "https://www.mako.co.il/mako-vod-channel2-news/meet_the_press", SAT, (21, 3), "/2/2", 15, "פגוש את העיתונות"),
]
EVERY = {"12": 20 * 60, "13": 10 * 60}  # כל כמה זמן לבדוק בתוך החלון
FROM_DAY = {"OLF": -1}  # אולפן שישי משודר בשישי ועולה במוצ"ש - הפרק מותר מיום לפני הערב
MAKO_GAP = 65  # שניות בין פניות למאקו
MAKO_LISTS = ["https://www.mako.co.il/mako-vod-channel2-news", "https://www.mako.co.il/mako-vod-keshet"]

LOG = []


def log(*a):
    line = datetime.now(IL).strftime("%d/%m %H:%M") + " " + " ".join(str(x) for x in a)
    print("[editions]", line, flush=True)
    LOG.append(line)
    del LOG[:-80]
    try:
        ym("UploadTextFile", what="ivr2:/AgentCode/editions_status.txt", contents="\n".join(LOG))
    except Exception:  # noqa: BLE001
        pass


def ym(name, **p):
    p["token"] = KEY
    req = urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save(st):
    json.dump(st, open(STATE + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(STATE + ".tmp", STATE)


# ---------- כלים ----------
def ffmpeg():
    """אותו ffmpeg סטטי שסוכן הרדיו מוריד לתיקייה bin."""
    ff = os.path.join(BIN, "ffmpeg")
    if not os.path.exists(ff):
        import io, platform, stat, tarfile
        os.makedirs(BIN, exist_ok=True)
        arm = "aarch64" in platform.machine() or "arm" in platform.machine()
        t = subprocess.run(["curl", "-sSL", "-m", "600", "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-"
                            + ("arm64" if arm else "amd64") + "-static.tar.xz"], capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(t), mode="r:xz") as tf:
            m = next(x for x in tf.getmembers() if x.name.endswith("/ffmpeg"))
            open(ff, "wb").write(tf.extractfile(m).read())
        os.chmod(ff, os.stat(ff).st_mode | stat.S_IEXEC)
    return ff


def cffi():
    """curl_cffi (התחזות לכרום) - מותקן פעם אחת לתיקייה pylib. גרסה 0.7 עדיין תומכת בפייתון 3.8."""
    if PYLIB not in sys.path:
        sys.path.insert(0, PYLIB)
    try:
        from curl_cffi import requests as cr
        return cr
    except ImportError:
        pass
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--target", PYLIB,
                        "curl_cffi==0.7.4"], capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError("pip curl_cffi: " + (r.stderr or r.stdout)[-300:])
    import importlib
    importlib.invalidate_caches()
    from curl_cffi import requests as cr
    return cr


_last_mako = [0.0]


def mako_get(url):
    wait = _last_mako[0] + MAKO_GAP - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_mako[0] = time.time()
    r = cffi().get(url, impersonate="chrome", timeout=60,
                   headers={"Accept-Language": "he-IL,he;q=0.9,en;q=0.8", "Referer": "https://www.mako.co.il/"})
    t = r.text
    if r.status_code != 200 or re.search(r"perfdrive|radware|captcha", t[:5000] + r.url, re.I):
        raise RuntimeError(f"mako {r.status_code} {r.url[:80]} (חסימה?)")
    return t


def norm(t):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (t or "").replace("־", " "))).strip().lower()


def best(query, names):
    words = [w for w in norm(query).split() if len(w) > 1]
    bi, bs = -1, 0
    for i, n in enumerate(names):
        nn = norm(n)
        sc = sum(1 for w in words if w in nn) + (0.5 if norm(query) in nn else 0)
        if sc > bs:
            bi, bs = i, sc
    return bi


# ---------- 13 ----------
def kaltura(path, body):
    req = urllib.request.Request("https://5031.frp1.ott.kaltura.com/api_v3/service/" + path,
                                 json.dumps(dict({"apiVersion": "5.4.0"}, **body)).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode()).get("result") or {}


def drucker_show(ks):
    """השם הנוכחי של התוכנית היומית של רביב דרוקר (משתנה) - לפי לוח השידורים של 13."""
    try:
        objs = kaltura("asset/action/list", {"ks": ks, "filter": {"objectType": "KalturaSearchAssetFilter",
                       "kSql": "(and description~'רביב דרוקר' asset_type='epg')", "orderBy": "START_DATE_DESC"},
                       "pager": {"objectType": "KalturaFilterPager", "pageSize": 50, "pageIndex": 1}}).get("objects") or []
        now = time.time()
        objs = [o for o in objs if (o.get("startDate") or 0) <= now and "יומית" in (o.get("description") or "")
                and not re.search(r"המקור|הקברניטים", o.get("name") or "")]
        if objs:
            return max(objs, key=lambda o: o.get("startDate") or 0)["name"].strip()
    except Exception as e:  # noqa: BLE001
        log("drucker epg", str(e)[:150])
    return "אזור בחירה"


def latest13(query):
    """(מזהה, כותרת, זמן פרסום, entryId) של הפרק האחרון בסדרה, או None."""
    ks = kaltura("ottuser/action/anonymousLogin", {"partnerId": 5031})["ks"]
    if query == "@drucker":
        query = drucker_show(ks)
    q = re.sub(r"['\"\\()]", " ", query).strip()

    def lst(ksql, size=50):
        return kaltura("asset/action/list", {"ks": ks, "filter": {"objectType": "KalturaSearchAssetFilter", "kSql": ksql,
                       "typeIn": "1268", "orderBy": "CREATE_DATE_DESC"},
                       "pager": {"objectType": "KalturaFilterPager", "pageSize": size, "pageIndex": 1}}).get("objects") or []
    meta = lambda o, k: str(((o.get("metas") or {}).get(k) or {}).get("value") or "")
    sname = lambda o: (((o.get("tags") or {}).get("SeriesName") or {}).get("objects") or [{}])[0].get("value") or ""
    series = {}
    for o in lst(f"(or SeriesName~'{q}' name~'{q}')"):
        if meta(o, "SeriesID"):
            series.setdefault(meta(o, "SeriesID"), sname(o) or o.get("name", ""))
    ids, names = list(series), list(series.values())
    i = best(query, names)
    if i < 0:
        return None
    eps = sorted(lst(f"(and SeriesID='{ids[i]}')"), key=lambda o: max(o.get("createDate") or 0, o.get("startDate") or 0),
                 reverse=True)
    if not eps:
        return None
    o = eps[0]
    eid = o.get("entryId") or next((m.get("externalId") for m in o.get("mediaFiles") or [] if m.get("externalId")), "")
    return str(o["id"]), f"{names[i]} - {o.get('name', '')}", max(o.get("createDate") or 0, o.get("startDate") or 0), eid


def kapi(params):
    q = urllib.parse.urlencode(dict(params, format=1))
    with urllib.request.urlopen("https://cdnapisec.kaltura.com/api_v3/?" + q, timeout=40) as r:
        return json.loads(r.read().decode())


def grab13(eid, out, secs=None):
    """ישירות מ-Kaltura עם ffmpeg (בלי yt-dlp - חוסך זיכרון בשרת): מפתח ווידג'ט אנונימי -> רשימת האיכויות
    -> הקובץ בגודל הקטן ביותר (בשביל הקו מספיק השמע) כהורדה ישירה."""
    ref = urllib.parse.quote(base64.b64encode(b"https://13tv.co.il/").decode())
    hd = {"Referer": "https://13tv.co.il/", "Origin": "https://13tv.co.il"}
    base = f"https://cdnapisec.kaltura.com/p/2748741/sp/274874100/playManifest/entryId/{eid}"
    urls, info = [], ""
    try:
        ks = kapi({"service": "session", "action": "startWidgetSession", "widgetId": "_2748741"}).get("ks", "")
        fl = kapi({"service": "flavorAsset", "action": "getByEntryId", "entryId": eid, "ks": ks})
        fl = [f for f in (fl if isinstance(fl, list) else []) if f.get("status") == 2 and f.get("id")]
        fl.sort(key=lambda f: (f.get("isOriginal") is True, f.get("bitrate") or 0))
        info = f"ks={'yes' if ks else 'no'} flavors={len(fl)} " + ",".join(
            f"{f.get('id')}:{f.get('bitrate')}k/{f.get('height')}p/{f.get('fileExt')}" for f in fl[:6])
        try:  # הכתובת כמו ש-yt-dlp בונה: dataUrl של הפרק + /flavorId/<id> + /ks/<ks> + ?referrer=
            data_url = kapi({"service": "baseEntry", "action": "get", "entryId": eid, "ks": ks}).get("dataUrl") or ""
        except Exception:  # noqa: BLE001
            data_url = ""
        data_url = data_url or f"{base}/format/url/protocol/https"
        info += " | " + data_url[-70:]
        hls = data_url.replace("format/url", "format/applehttp")  # כמו yt-dlp: ה-HLS עם המפתח
        for m3 in (f"{hls}/ks/{ks}?referrer={ref}", f"{hls}/ks/{ks}/a.m3u8?referrer={ref}"):
            low = lowest_variant(m3, hd)
            if low:
                urls.append(low)
            urls.append(m3)
    except Exception as e:  # noqa: BLE001
        info = "api: " + str(e)[:150]
    urls += [f"{base}/format/applehttp/protocol/https/a.m3u8?referrer={ref}"]
    errs = []
    for u in urls:
        try:
            to_mp3(u, out, secs, hd)
            return
        except Exception as e:  # noqa: BLE001
            errs.append(re.sub(r"/ks/[^/?]+", "/ks/…", u)[-90:] + " -> " + probe(u, hd) + " | " + str(e)[-120:])
    raise RuntimeError(info + " || " + " || ".join(errs))


def lowest_variant(master, headers):
    """מרשימת האיכויות של HLS - הכתובת של האיכות הנמוכה (לקו צריך רק את השמע; חוסך הורדה)."""
    try:
        r = subprocess.run(["curl", "-sSL", "-m", "30", "-A", UA] + sum((["-H", f"{k}: {v}"] for k, v in headers.items()), [])
                           + ["-w", "\n%{url_effective}", master], capture_output=True)
        body, final = r.stdout.decode("utf-8", "replace").rsplit("\n", 1)
        if "#EXTM3U" not in body:
            return ""
        best_bw, best_u, lines = None, "", body.splitlines()
        for i, l in enumerate(lines):
            m = re.search(r"BANDWIDTH=(\d+)", l)
            if l.startswith("#EXT-X-STREAM-INF") and m and i + 1 < len(lines) and lines[i + 1].strip():
                if best_bw is None or int(m.group(1)) < best_bw:
                    best_bw, best_u = int(m.group(1)), urllib.parse.urljoin(final, lines[i + 1].strip())
        return best_u
    except Exception:  # noqa: BLE001
        return ""


def probe(url, headers):
    """מה השרת מחזיר בכתובת (קוד, סוג, תחילת התוכן) - לאבחון."""
    try:
        r = subprocess.run(["curl", "-sSL", "-m", "30", "-A", UA, "-r", "0-300", "-o", "-", "-w", "\n%{http_code} %{content_type}"]
                           + sum((["-H", f"{k}: {v}"] for k, v in headers.items()), []) + [url], capture_output=True)
        body = r.stdout.decode("utf-8", "replace")
        return re.sub(r"\s+", " ", body[-60:] + " :: " + body[:120])
    except Exception as e:  # noqa: BLE001
        return "probe " + str(e)[:80]


# ---------- 12 ----------
def mako_links(html_text):
    """[(קישור, טקסט)] מכל ה-<a> בדף (הטקסט: תוכן הקישור, או alt/title של תמונה בתוכו)."""
    out = []
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.S | re.I):
        href, inner = html.unescape(m.group(1)), m.group(2)
        txt = re.sub(r"<[^>]+>", " ", inner)
        if not txt.strip():
            a = re.search(r'(?:alt|title|aria-label)="([^"]+)"', inner + m.group(0))
            txt = a.group(1) if a else ""
        txt = re.sub(r"\s+", " ", html.unescape(txt)).strip()
        out.append((urllib.parse.urljoin("https://www.mako.co.il/", href), txt))
    return out


def find_show12(name, st):
    cache = st.setdefault("show12", {})
    if name in cache:
        return cache[name]
    shows = {}
    for lp in MAKO_LISTS:
        for h, t in mako_links(mako_get(lp)):
            if re.match(r"https://www\.mako\.co\.il/mako-vod-(?:keshet|channel2-news)/[^/?#]+/?$", h) and t:
                t = re.split(r"\s+-\s+", t)[0].strip()  # "צא מזה - מצביע - HD, WOW" -> "צא מזה"
                shows.setdefault(h.rstrip("/"), t)
    urls, names = list(shows), list(shows.values())
    i = best(name, [f"{n} {u.rsplit('/', 1)[-1].replace('_', ' ')}" for u, n in zip(urls, names)])
    if i < 0:
        raise RuntimeError(f"לא נמצאה תוכנית '{name}' במאקו ({len(shows)} תוכניות)")
    cache[name] = urls[i]
    log(f"12: '{name}' -> {names[i]} {urls[i]}")
    return urls[i]


def date_of(t):
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", t or "")
    if not m:
        return None
    y = int(m.group(3))
    try:
        return datetime(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)), 20, 0, tzinfo=IL).timestamp()
    except ValueError:
        return None


def latest12(what, st):
    """(מזהה, כותרת, זמן לפי התאריך בכותרת או None, קישור לפרק)."""
    show_url = what if what.startswith("http") else find_show12(what, st)
    base = show_url.split("mako.co.il", 1)[-1].rstrip("/")
    eps, seen = [], set()
    for h, t in mako_links(mako_get(show_url)):
        clean = h.split("#")[0].split("?")[0]
        if re.search(r"/VOD-\w+\.htm$", clean) and base in clean:
            if clean in seen:  # אותו פרק מופיע כמה פעמים (תמונה + כותרת) - שומרים את הטקסט הארוך
                for k, e in enumerate(eps):
                    if e[1] == clean and len(t) > len(e[0]):
                        eps[k] = (t, clean)
                continue
            seen.add(clean)
            eps.append((t, clean))
    if not eps:
        return None
    dated = [e for e in eps if date_of(e[0])]
    t, u = max(dated, key=lambda e: date_of(e[0])) if dated else eps[0]
    return re.search(r"VOD-(\w+)\.htm", u).group(1), t, date_of(t), u


def mako_stream(ep_url):
    page = mako_get(ep_url)
    paths = list(dict.fromkeys(p.replace("\\/", "/") for p in re.findall(r'(/i/[^"\'\s<>]+?\.csmil/master\.m3u8)', page.replace("\\/", "/"))))
    if not paths:
        raise RuntimeError("לא נמצא נתיב csmil בדף הפרק (%d תווים)" % len(page))
    path = paths[0]
    tk = mako_get("https://mass.mako.co.il/ClicksStatistics/entitlementsServicesV2.jsp?et=gt&lp="
                  + urllib.parse.quote(path, safe="") + "&rv=AKAMAI")
    try:
        ticket = urllib.parse.unquote(json.loads(tk)["tickets"][0]["ticket"])
    except Exception:  # noqa: BLE001
        raise RuntimeError("כרטיס: " + tk[:200])
    return "https://mako-vod.akamaized.net" + path + "?" + ticket


def grab12(ep_url, out, secs=None):
    to_mp3(mako_stream(ep_url), out, secs, {"Referer": "https://www.mako.co.il/", "Origin": "https://www.mako.co.il"})


# ---------- הורדה והעלאה ----------
def to_mp3(url, out, secs, headers):
    hd = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    cmd = [ffmpeg(), "-y", "-loglevel", "warning", "-user_agent", UA, "-headers", hd, "-i", url]
    if secs:
        cmd += ["-t", str(secs)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-b:a", "24k", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3 * 3600)
    if r.returncode or not os.path.exists(out) or os.path.getsize(out) < 20000:
        raise RuntimeError(f"ffmpeg rc={r.returncode}: " + (r.stderr or "")[-300:])


def next_slot(folder, keep):
    d = ym("GetIVR2Dir", path="ivr2:" + folder)
    nums = sorted(int(m.group(1)) for f in d.get("files") or [] if (m := re.match(r"^(\d{3})\.wav$", f.get("name", ""))))
    for n in nums[:max(0, len(nums) - (keep - 1))]:
        ym("FileAction", action="delete", what=f"ivr2:{folder}/{n:03d}.wav")
    return f"{(nums[-1] + 1) if nums else 100:03d}"


def upload(path, target):
    out = subprocess.run(["curl", "-sS", "-m", "3600", "-F", f"token={KEY}", "-F", f"path={target}.wav",
                          "-F", "convertAudio=1", "-F", f"file=@{path};filename=x.mp3", API + "UploadFile"],
                         capture_output=True, text=True).stdout
    if '"OK"' not in out:
        raise RuntimeError("upload: " + out[:200])


def selftest(st):
    """פעם אחת: 60 שניות מהפרק האחרון בכל ערוץ -> AgentCode/test_ed12 / test_ed13 (לבדיקה שהדרך עובדת)."""
    tests = {"13": lambda: latest13("המהדורה המרכזית"), "12": lambda: latest12(JOBS[0][2], st)}
    for name, fn in [(k, tests[k]) for k in SELFTEST_CH]:
        try:
            ep = fn()
            if not ep:
                log(f"TEST {name}: לא נמצא פרק")
                continue
            log(f"TEST {name}: {ep[1][:70]} | id {ep[0]} | {ep[3][:80]}")
            t0 = time.time()
            (grab13 if name == "13" else grab12)(ep[3], TMP, 60)
            upload(TMP, f"ivr2:/AgentCode/test_ed{name}")
            log(f"TEST {name}: OK - 60 שניות ({os.path.getsize(TMP) // 1024}KB, {time.time() - t0:.0f} שנ') -> AgentCode/test_ed{name}.wav")
        except Exception as e:  # noqa: BLE001
            log(f"TEST {name}: FAILED {str(e)[:900]}")
        finally:
            if os.path.exists(TMP):
                os.remove(TMP)
    st["selftest"] = SELFTEST
    save(st)


def evening(days, h1, h2, now):
    """תאריך הערב שהחלון שייך אליו, או None אם עכשיו מחוץ לחלון."""
    if now.weekday() in days and now.hour >= h1:
        return now.date()
    y = now - timedelta(days=1)
    if y.weekday() in days and now.hour < h2:
        return y.date()
    return None


def cycle(st):
    now = datetime.now(IL)
    for jid, ch, what, days, (h1, h2), folder, keep, label in JOBS:
        ev = evening(days, h1, h2, now)
        js = st.setdefault("jobs", {}).setdefault(jid, {"done": [], "day": "", "t": 0})
        if not ev or js["day"] == str(ev) or time.time() - js["t"] < EVERY[ch]:
            continue
        js["t"] = time.time()
        try:
            ep = latest13(what) if ch == "13" else latest12(what, st)
        except Exception as e:  # noqa: BLE001
            log(jid, "check failed:", str(e)[:200])
            save(st)
            continue
        if not ep:
            log(jid, "לא נמצא פרק")
            save(st)
            continue
        eid, title, ts, src = ep
        if eid in js["done"]:
            continue
        start = datetime(ev.year, ev.month, ev.day, tzinfo=IL) + timedelta(days=FROM_DAY.get(jid, 0))
        if ts and ts < start.timestamp():
            continue  # הפרק האחרון הוא מערב קודם - של הערב עוד לא עלה
        if not ts and not js["done"]:  # בלי תאריך ובלי היסטוריה: רק זוכרים את מה שקיים, לא מעלים
            js["done"] = [eid]
            log(jid, "מבט ראשון (בלי תאריך):", title[:60])
            save(st)
            continue
        log(jid, "חדש:", title[:70], "- מוריד")
        try:
            t0 = time.time()
            (grab13 if ch == "13" else grab12)(src, TMP)
            mb = os.path.getsize(TMP) / 1048576
            slot = next_slot(folder, keep)
            upload(TMP, f"ivr2:{folder}/{slot}")
            js["done"] = (js["done"] + [eid])[-30:]
            js["day"] = str(ev)
            log(jid, f"הועלה -> {folder}/{slot} ({mb:.1f}MB, {(time.time() - t0) / 60:.0f} דק') {title[:60]}")
        except Exception as e:  # noqa: BLE001
            log(jid, "FAILED", str(e)[:250])
        finally:
            if os.path.exists(TMP):
                os.remove(TMP)
        save(st)


if __name__ == "__main__":
    log("editions agent started")
    st = load()
    if st.get("selftest") != SELFTEST:
        selftest(st)
    while True:
        try:
            cycle(st)
        except Exception as e:  # noqa: BLE001
            log("cycle error", str(e)[:200])
        time.sleep(120)
