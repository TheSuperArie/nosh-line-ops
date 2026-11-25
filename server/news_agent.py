"""סוכן המבזקים לקו נווה שלום (שלוחה 1).
מאזין לזרם האירועים של אותנטי ניוז (מתעדכן מיד כשעולה מבזק), ובנוסף בודק כל דקה ליתר ביטחון.
כל מבזק חדש מוקרא מראש לקובץ שמע (edge-tts, קול גבר "אברי") ועולה לשלוחה 1 - החדש ביותר ראשון. שומר עד 250 מבזקים מ-15 השעות האחרונות.
אם ההקראה נכשלת - נופל לקובץ הקראה של ימות (.tts) כמו קודם.
בשבת ויום טוב - לא עושה כלום (שומר שבת), ובמוצאי שבת משלים.
הפעלה: YM_TOKEN=... python3 news_agent.py        (או --once לריצה אחת)"""
import asyncio, json, os, re, subprocess, sys, tempfile, time, threading, uuid, urllib.parse, urllib.request
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8 (אובונטו 20.04)
    from backports.zoneinfo import ZoneInfo

SITE = os.environ.get("NEWS_SITE", "https://authenti.newsupdates.click")
API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip()
EXT = os.environ.get("NEWS_EXT", "/8/1/1")  # 25/11: אותנטי ניוז עבר ל-8/1/1 (בשלוחה 1 עכשיו הגזרה)
KEEP = int(os.environ.get("NEWS_KEEP", "250" if EXT == "/1" else "30"))
STATE = os.environ.get("NEWS_STATE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_auth811_state.json"))  # מצב חדש אחרי ההחלפה
IL = ZoneInfo("Asia/Jerusalem")
VOICE = os.environ.get("NEWS_VOICE", "fr-FR-RemyMultilingualNeural")  # נבחר 13/11 (קודם: he-IL-AvriNeural)
RATE = os.environ.get("NEWS_RATE", "+12%")
AUTHORS = [a.strip() for a in os.environ.get("NEWS_AUTHORS", "").split(",") if a.strip()]  # רק ערוצים אלה (שלוחות שונות)
SAY_CH = os.environ.get("NEWS_SAY_CH", "1") != "0"
UA = {"User-Agent": "Mozilla/5.0 (nosh-line news agent)"}
lock = threading.Lock()

def log(*a):
    print(datetime.now(IL).strftime("%d/%m %H:%M:%S"), *a, flush=True)

# ---------- שומר שבת ----------
def shabbat_ok(now=None):
    try:
        from zmanim.util.geo_location import GeoLocation
        from zmanim.zmanim_calendar import ZmanimCalendar
        from zmanim.hebrew_calendar.jewish_calendar import JewishCalendar
    except ImportError:  # בלי הספרייה - לא מסתכנים: חוסמים מערב שבת 12:00 עד מוצ"ש 21:00
        now = now or datetime.now(IL)
        wd = now.weekday()  # שני=0 ... שישי=4 שבת=5
        return not ((wd == 4 and now.hour >= 12) or (wd == 5 and now.hour < 21))
    loc = GeoLocation("Jerusalem", 31.778, 35.235, "Asia/Jerusalem", elevation=754)
    now = now or datetime.now(IL)
    d = now.date()
    sunset = lambda x: ZmanimCalendar(geo_location=loc, date=x).sunset()
    assur = lambda x: JewishCalendar(x, in_israel=True).is_assur_bemelacha()
    if assur(d) and now < sunset(d) + timedelta(minutes=75):
        return False
    if assur(d + timedelta(days=1)) and now >= sunset(d) - timedelta(minutes=45):
        return False
    return True

# ---------- ימות ----------
def ym(name, **p):
    p["token"] = KEY
    req = urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

# ---------- אותנטי ----------
def get_json(path):
    with urllib.request.urlopen(urllib.request.Request(SITE + path, headers=UA), timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def clean(text):
    t = re.sub(r"\[[a-z]+-embedded#?[^\]]*\]\([^)]*\)", " ", text or "")  # סרטונים/תמונות מוטמעים - לא מקריאים
    t = re.sub(r"<[^>]*>", " ", t)                                   # תגיות HTML (אודיו/וידאו)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)                 # קישורי מרקדאון
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>~|]+", " ", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", t)  # אימוג'י
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r"\.{2,}", ".", re.sub(r"\s{2,}", " ", t)).strip(" .")
    return t[:700]

MEDIA = os.environ.get("NEWS_MEDIA") == "1"  # להעלות גם סרטונים/הקלטות (את הקול שלהם) - בחתול השחור ובגזרה

def media_url(m):
    t = m.get("text") or ""
    mm = re.search(r"\[(?:video|audio)-embedded#?[^\]]*\]\((https?://[^)\s#]+)", t)
    if mm:
        return mm.group(1)
    mm = re.search(r"<(?:video|audio)[^>]*src=[\"'](https?://[^\"']+)", t)
    if mm:
        return mm.group(1)
    f = m.get("file") or {}
    if f.get("url") and re.match(r"(video|audio)", f.get("filetype") or ""):
        return f["url"] if f["url"].startswith("http") else SITE + f["url"]
    return None

def to_item(m):
    if not isinstance(m, dict) or m.get("deleted") or m.get("is_ads") or m.get("is_advertisement"):
        return None
    txt = clean(m.get("text"))
    media = media_url(m) if MEDIA else None
    if len(txt) < 15 and not media:
        return None
    try:
        ts = datetime.fromisoformat(re.sub(r"\.(\d{6})\d*", r".\1", m["timestamp"]).replace("Z", "+00:00")).astimezone(IL)
        when = ts.strftime("%H:%M")
    except Exception:  # noqa
        when = ""
    ch = (m.get("channel") or m.get("author") or "").strip()
    if AUTHORS and ch not in AUTHORS:
        return None
    if ch and txt.startswith(ch):  # "הפרגוד: הפרגוד: ..." - לא לחזור על שם הערוץ
        txt = txt[len(ch):].lstrip(" :.-")
    head = ", ".join(x for x in (f"בשעה {when}" if when else "", ch if SAY_CH else "") if x)
    try:
        epoch = ts.timestamp()
    except Exception:  # noqa
        epoch = time.time()
    it = {"id": m.get("id"), "say": (head + ": " if head else "") + (txt + "." if len(txt) >= 3 else ""), "txt": txt, "ts": epoch, "ch": ch}
    if media:
        it["media"] = media
    return it

# ---------- סינון כפילויות: אותו מבזק מכמה כתבים/ערוצים ----------
DUP_WINDOW = int(os.environ.get("NEWS_DUP_MIN", "60")) * 60      # דומה מאוד - בתוך שעה
EXACT_WINDOW = int(os.environ.get("NEWS_EXACT_MIN", "360")) * 60  # אותו טקסט ממש - בתוך 6 שעות

def _norm(t):
    t = re.sub(r"[\u0591-\u05C7]", "", t or "")             # ניקוד וטעמים
    t = re.sub(r"[^0-9A-Za-z\u05D0-\u05EA ]+", " ", t)      # בלי סימני פיסוק
    return re.sub(r"\s+", " ", t).strip()

def similar(a, b, exact_only=False):
    import difflib
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 25 and (a in b or b in a)):  # אותו טקסט / אחד הוא השני + תוספת
        return True
    if min(len(a), len(b)) >= 40 and a[:40] == b[:40]:                 # אותה פתיחה (הודעות דוברות)
        return True
    if exact_only:
        return False
    wa, wb = set(a.split()), set(b.split())
    if len(wa & wb) / max(1, len(wa | wb)) >= 0.7:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8

def dedupe(new, recent):
    """מחזיר (לפרסום, כפולים). recent = רשימת [טקסט, זמן] של כל מה שהתקבל לאחרונה (גם מה שסונן)."""
    keep, dup = [], []
    pool = [(r[0], r[1]) for r in recent if r and r[0]]
    for it in sorted(new, key=lambda x: x.get("ts") or 0):
        t, ts = it.get("txt") or "", it.get("ts") or 0
        twin = next((p for p in pool if (abs(ts - p[1]) <= DUP_WINDOW and similar(t, p[0])) or
                     (abs(ts - p[1]) <= EXACT_WINDOW and similar(t, p[0], True))), None)
        (dup if twin else keep).append(it)
        if twin:
            log(f"duplicate skipped: [{it.get('ch')}] {t[:60]}")
        pool.append((t, ts))
    return keep, dup


def latest(n=150 if AUTHORS else 25):
    j = get_json(f"/api/messages?offset=0&limit={n}&direction=desc")
    arr = j if isinstance(j, list) else (j.get("messages") or j.get("data") or [])
    return [x for x in (to_item(m) for m in arr) if x]

# ---------- מצב + העלאה ----------
def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa
        return {"items": []}

def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)

# ---------- הקראה מראש ----------
_tts = None
def tts_lib():
    """טוען את edge-tts (ומתקין אותו לסביבה של הסוכן אם חסר)."""
    global _tts
    if _tts is None:
        try:
            import edge_tts
        except ImportError:
            log("installing edge-tts...")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "edge-tts"], capture_output=True, text=True, timeout=600)
            if r.returncode:
                log("pip error:", (r.stderr or r.stdout)[-300:])
            import importlib, site
            importlib.invalidate_caches()
            for d in site.getsitepackages() + [site.getusersitepackages()]:
                if d not in sys.path:
                    sys.path.append(d)
            try:
                import edge_tts
            except ImportError as e:
                log("edge-tts unavailable:", e)
                edge_tts = False
        _tts = edge_tts
    return _tts

def render(text):
    """מחזיר בתים של mp3, או None אם נכשל."""
    lib = tts_lib()
    if not lib:
        return None
    path = os.path.join(tempfile.gettempdir(), f"nosh_{uuid.uuid4().hex}.mp3")
    try:
        asyncio.run(asyncio.wait_for(lib.Communicate(text, VOICE, rate=RATE).save(path), 60))  # לא להיתקע
        data = open(path, "rb").read()
        if len(data) < 1000:
            log("tts: empty audio", len(data))
            return None
        return data
    except Exception as e:  # noqa
        log("tts error:", str(e)[:150])
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

def upload_audio(path, data):
    b = "----nosh" + uuid.uuid4().hex
    parts = []
    for k, v in (("token", KEY), ("path", path), ("convertAudio", "1")):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(API + "UploadFile", data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def ffmpeg():
    """ffmpeg סטטי בתיקיית הסוכנים (אותו אחד של סוכן הרדיו)."""
    import io, platform, stat, tarfile
    ff = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg")
    if not os.path.exists(ff):
        os.makedirs(os.path.dirname(ff), exist_ok=True)
        arm = "aarch64" in platform.machine() or "arm" in platform.machine()
        t = subprocess.run(["curl", "-sSL", "-m", "600", "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-"
                            + ("arm64" if arm else "amd64") + "-static.tar.xz"], capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(t), mode="r:xz") as tf:
            mb = next(x for x in tf.getmembers() if x.name.endswith("/ffmpeg"))
            open(ff, "wb").write(tf.extractfile(mb).read())
        os.chmod(ff, os.stat(ff).st_mode | stat.S_IEXEC)
    return ff

def media_audio(url, say_mp3):
    """מוריד סרטון/הקלטה, מוציא את הקול (עד 15 דקות), ומצרף אחרי ההקראה (אם יש). מחזיר mp3 או None."""
    tmp = tempfile.gettempdir()
    src = os.path.join(tmp, f"nosh_m_{uuid.uuid4().hex}")
    out = src + ".mp3"
    try:
        r = subprocess.run(["curl", "-sSL", "-m", "300", "--max-filesize", "150000000", "-A", UA.get("User-Agent", "Mozilla/5.0"),
                            "-o", src, url], capture_output=True)
        if r.returncode or not os.path.exists(src) or os.path.getsize(src) < 2000:
            log("media download failed", url[-60:], r.returncode)
            return None
        cmd = [ffmpeg(), "-y", "-loglevel", "error"]
        if say_mp3:
            sp = src + "_say.mp3"
            open(sp, "wb").write(say_mp3)
            cmd += ["-i", sp, "-i", src, "-filter_complex",
                    "[0:a]aresample=16000,aformat=channel_layouts=mono[a];[1:a]aresample=16000,aformat=channel_layouts=mono,atrim=0:900[b];[a][b]concat=n=2:v=0:a=1[o]",
                    "-map", "[o]"]
        else:
            cmd += ["-i", src, "-vn", "-t", "900"]
        cmd += ["-ac", "1", "-ar", "16000", "-b:a", "32k", out]
        c = subprocess.run(cmd, capture_output=True, timeout=900)
        if c.returncode or not os.path.exists(out):
            log("media ffmpeg failed", c.stderr.decode("utf-8", "replace")[-150:])
            return None
        return open(out, "rb").read()
    except Exception as e:  # noqa
        log("media error", str(e)[:120])
        return None
    finally:
        for f in (src, src + "_say.mp3", out):
            try:
                os.remove(f)
            except OSError:
                pass

def put_item(i, it):
    """מעלה מבזק למקום i: שמע מוכן, ואם לא הצליח - טקסט להקראה של ימות."""
    if it.get("media"):
        say = render(it["say"]) if it["say"].strip() else None
        data = media_audio(it["media"], say)
        if data:
            r = upload_audio(f"ivr2:{EXT}/{i:03d}.wav", data)
            if r.get("responseStatus") == "OK":
                ym("FileAction", action="delete", what=f"ivr2:{EXT}/{i:03d}.tts")
                return "wav"
        if len(it.get("txt") or "") < 15:
            return "skip"  # סרטון בלי טקסט שלא הצליח - מדלגים
    data = render(it["say"])
    if data:
        r = upload_audio(f"ivr2:{EXT}/{i:03d}.wav", data)
        if r.get("responseStatus") == "OK":
            ym("FileAction", action="delete", what=f"ivr2:{EXT}/{i:03d}.tts")  # אם נשאר טקסט ישן באותו מקום
            return "wav"
        log("audio upload error", r)
    ym("FileAction", action="delete", what=f"ivr2:{EXT}/{i:03d}.wav")
    r = ym("UploadTextFile", what=f"ivr2:{EXT}/{i:03d}.tts", contents=it["say"])
    return "tts" if r.get("responseStatus") == "OK" else None

MAX_AGE = float(os.environ.get("NEWS_MAX_HOURS", "15" if EXT == "/1" else "72")) * 3600  # שלוחה 1: 15 שעות (ביקש 22/11)

def publish(st, new_items):
    """שמות קבועים: כל מבזק חדש מקבל מספר גבוה מהקודם (השלוחה מנגנת מהגבוה לנמוך), והקיימים לא זזים -
    כך מי שבאמצע האזנה לא קופץ. נמחקים הישנים מעבר ל-KEEP או מעל 3 ימים. מתחילים שוב מ-000 רק כשמגיעים ל-990."""
    files = st.get("files")
    if files is None:  # מעבר מהשיטה הישנה: מוחקים הכל ומעלים מחדש מהישן לחדש
        for i in range(max(st.get("published", 0), KEEP, 60)):
            for ext in ("wav", "tts"):
                ym("FileAction", action="delete", what=f"ivr2:{EXT}/{i:03d}.{ext}")
        files, new_items, st["next"] = [], sorted(st["items"][:KEEP], key=lambda x: x["id"] or 0), 0
    nxt = st.get("next", 0)
    if nxt + len(new_items) > 990:  # מתחילים מחדש מ-000 (נדיר) - מעלים את הקיימים מחדש בסדר
        for f in files:
            ym("FileAction", action="delete", what=f"ivr2:{EXT}/{f['n']:03d}.{f['k']}")
        new_items = sorted(st["items"][:KEEP], key=lambda x: x["id"] or 0)
        files, nxt = [], 0
    for it in sorted(new_items, key=lambda x: x["id"] or 0):
        k = put_item(nxt, it)
        if k == "skip":
            continue
        if not k:
            st["files"], st["next"] = files, nxt + 1
            return False
        files.append({"n": nxt, "k": k, "id": it["id"], "ts": it.get("ts") or time.time()})
        nxt += 1
    old = [f for f in files[:-KEEP]] + [f for f in files[-KEEP:] if time.time() - f["ts"] > MAX_AGE]
    for f in old:
        ym("FileAction", action="delete", what=f"ivr2:{EXT}/{f['n']:03d}.{f['k']}")
    gone = {id(f) for f in old}
    st["files"] = [f for f in files if id(f) not in gone]
    st["next"] = nxt
    return True

def sync(reason=""):
    if not shabbat_ok():
        return
    with lock:
        st = load()
        known = {it["id"] for it in st["items"]} | set(st.get("dupes") or [])
        new = [it for it in latest() if it["id"] not in known]
        if not new:
            return
        recent = st.get("recent") or [[i.get("txt"), i.get("ts")] for i in st["items"] if i.get("txt") and i.get("ts")]
        new, dup = dedupe(new, recent)
        cut = time.time() - EXACT_WINDOW
        st["recent"] = [r for r in recent if r[1] and r[1] > cut][-400:] + [[i["txt"], i["ts"]] for i in new + dup]
        if dup:
            st["dupes"] = ((st.get("dupes") or []) + [d["id"] for d in dup])[-500:]
        if not new:
            save(st)
            return
        st["items"] = sorted(new + st["items"], key=lambda x: x["id"] or 0, reverse=True)[:KEEP]
        ok = publish(st, new)
        save(st)
        log(f"{len(new)} new flash(es) {'uploaded' if ok else 'FAILED'} ({reason}); skipped {len(dup)} duplicate(s); files {len(st.get('files') or [])}, next {st.get('next')}")

def repair():
    """מבזקים שעלו כטקסט (קול של ימות) בזמן תקלה בהקראה - מקליטים מחדש בקול הרגיל, עד 3 בכל דקה."""
    if not shabbat_ok():
        return
    with lock:
        st = load()
        by_id = {it["id"]: it for it in st.get("items") or []}
        fixed = 0
        for f in st.get("files") or []:
            if f.get("k") != "tts" or fixed >= 3:
                continue
            it = by_id.get(f.get("id"))
            data = render(it["say"]) if it and it.get("say", "").strip() else None
            if not data:
                break  # ההקראה עדיין לא עובדת - ננסה בדקה הבאה
            r = upload_audio(f"ivr2:{EXT}/{f['n']:03d}.wav", data)
            if r.get("responseStatus") == "OK":
                ym("FileAction", action="delete", what=f"ivr2:{EXT}/{f['n']:03d}.tts")
                f["k"] = "wav"
                fixed += 1
        if fixed:
            save(st)
            log(f"re-voiced {fixed} flash(es) that had fallen back to ימות TTS")

def sweep():
    """מוחק מהשלוחה קבצי מבזקים "יתומים" שהסוכן לא עוקב אחריהם (שאריות מתקלות העלאה/מחיקה).
    בלי זה, אחרי שהמספור חוזר ל-000, יתומים עם מספר גבוה נשמעים ראשונים (השלוחה מנגנת מהגבוה לנמוך)."""
    if not shabbat_ok():
        return
    with lock:
        st = load()
        tracked = {(f["n"], f["k"]) for f in st.get("files") or []}
        if not tracked:
            return
        d = ym("GetIVR2Dir", path=f"ivr2:{EXT}")
        if d.get("responseStatus") != "OK":
            return
        gone = 0
        for f in (d.get("files") or []) + (d.get("ini") or []):
            m = re.match(r"^(\d{3})\.(wav|tts)$", f.get("name") or "")
            if m and (int(m.group(1)), m.group(2)) not in tracked:
                if ym("FileAction", action="delete", what=f"ivr2:{EXT}/{m.group(0)}").get("responseStatus") == "OK":
                    gone += 1
        if gone:
            log(f"sweep: deleted {gone} orphan file(s)")

def listen():
    """זרם האירועים - מתעורר מיד כשיש הודעה חדשה."""
    while True:
        try:
            if not shabbat_ok():
                time.sleep(300)
                continue
            req = urllib.request.Request(SITE + "/api/events", headers={**UA, "Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=120) as r:
                for raw in r:
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data:") and "heartbeat" not in line:
                        sync("event")
                    if not shabbat_ok():
                        break
        except Exception as e:  # noqa
            log("events reconnect:", str(e)[:100])
            time.sleep(10)

if __name__ == "__main__":
    if not KEY:
        sys.exit("YM_TOKEN חסר")
    if "--once" in sys.argv:
        sync("once")
        st = load()
        log(f"state: {len(st['items'])} items, published {st.get('published')}")
        sys.exit(0)
    log("news agent started")
    threading.Thread(target=listen, daemon=True).start()
    last_sweep = 0
    while True:
        try:
            sync("poll")
            repair()
            if time.time() - last_sweep > 600:
                last_sweep = time.time()
                sweep()
        except Exception as e:  # noqa
            log("poll error:", str(e)[:150])
        time.sleep(60)
