"""סוכן "עלון לשבת" (קולדצקי) לקו נווה שלום - רץ בשרת.
סקריפט של גוגל בחשבון של אריה מעלה את ה-PDF של העלון לתיקייה AgentCode בימות (בחלקים base64:
alon_<תאריך>_<i>of<n>.txt). הסוכן מחבר, מוציא טקסט (pypdf), מחלק למדורים לפי הכותרות הקבועות,
מקריא כל מדור (edge-tts, אותו קול כמו בקו) ומעלה לשלוחה 8/4/<מספר>. בכל שלוחה נשמרים 2 עלונים אחרונים.
בודק רק משני 17:00 עד רביעי 14:00 (העלון יוצא בשני-שלישי) + פעם אחת בהפעלה. בשבת הכל עוצר (bootstrap)."""
import asyncio, base64, io, json, os, platform, re, stat, subprocess, sys, tarfile, tempfile, time, urllib.parse, urllib.request, uuid
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "alon_state.json")
IL = ZoneInfo("Asia/Jerusalem")
VOICE = os.environ.get("NEWS_VOICE", "fr-FR-RemyMultilingualNeural")
RATE = os.environ.get("NEWS_RATE", "+12%")
KEEP = 2  # כמה עלונים נשמרים בכל שלוחה

# (שלוחה, שם להקראה, ביטוי שמזהה את תחילת המדור בטקסט) - הסדר נקבע ע"י אריה 18/11
SECTIONS = [
    ("1", "הגיגים", r"הגיגים המיועדים"),
    ("2", "היכן ניתן להשיג את העלון", r"היכן ניתן להשיג את העלון"),
    ("3", "התורה חסה", r"התורה חסה\s*:"),
    ("4", "רעיונות מהקוראים", r"מדור זה יש להימנע מלקרוא|רעיונות שנשלחו השבוע"),
    ("5", "וורטים על פרשת השבוע", r"וארטים על פרשת השבוע|ווארטים על פרשת"),
    ("6", "חידות", r"(?m)^[ \t]*חידות[ \t]*$|חידון שינון הפרשה"),
    ("7", "ידיעות חשובות ודברים מגדולי ישראל", r"ידיעות חשובות"),
    ("8", "מאמרי מוסר וסיפורים", r"מאמרי מוסר"),
    ("9", "הלכות", r"(?m)^[ \t]*הלכות[ \t]*$"),
    ("0", "שאלות גמרא פירוש רש\"י ותוספות", r"שאלות גפ[\"״'׳]{1,2}ת"),
]

STATUS = []


def log(*a):
    line = datetime.now(IL).strftime("%d/%m %H:%M") + " " + " ".join(str(x) for x in a)
    print("[alon]", line, flush=True)
    STATUS.append(line)
    del STATUS[:-60]
    try:
        ym("UploadTextFile", what="ivr2:/AgentCode/alon_status.txt", contents="\n".join(STATUS))
    except Exception:  # noqa: BLE001
        pass


def ym(name, **p):
    p["token"] = KEY
    req = urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode())
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"done": {}}


def save(st):
    json.dump(st, open(STATE + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(STATE + ".tmp", STATE)


def need(module, pkg):
    """מייבא מודול, ומתקין אותו לסביבה של הסוכן אם חסר."""
    try:
        return __import__(module)
    except ImportError:
        log("installing", pkg)
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg], capture_output=True, timeout=900)
        import importlib, site
        importlib.invalidate_caches()
        for d in site.getsitepackages() + [site.getusersitepackages()]:
            if d not in sys.path:
                sys.path.append(d)
        return __import__(module)


def ffmpeg():
    ff = os.path.join(DIR, "bin", "ffmpeg")
    if not os.path.exists(ff):
        os.makedirs(os.path.dirname(ff), exist_ok=True)
        arm = "aarch64" in platform.machine() or "arm" in platform.machine()
        t = subprocess.run(["curl", "-sSL", "-m", "600", "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-"
                            + ("arm64" if arm else "amd64") + "-static.tar.xz"], capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(t), mode="r:xz") as tf:
            m = next(x for x in tf.getmembers() if x.name.endswith("/ffmpeg"))
            open(ff, "wb").write(tf.extractfile(m).read())
        os.chmod(ff, os.stat(ff).st_mode | stat.S_IEXEC)
    return ff


# ---------- איסוף העלונים שהסקריפט של גוגל העלה ----------
def pending():
    """{מזהה: {"parts": {i: שם}, "n": n, "body": שם}} מתוך AgentCode."""
    out = {}
    d = ym("GetIVR2Dir", path="ivr2:/AgentCode")
    for f in (d.get("files") or []) + (d.get("ini") or []) + (d.get("html") or []):  # קבצי txt מופיעים ב-ini
        name = f.get("name") or ""
        m = re.match(r"^alon_(\d{4}-\d\d-\d\d_\d{4})_(\d+)of(\d+)\.txt$", name)
        if m:
            e = out.setdefault(m.group(1), {"parts": {}, "n": int(m.group(3))})
            e["parts"][int(m.group(2))] = name
            continue
        m = re.match(r"^alon_(\d{4}-\d\d-\d\d_\d{4})_body\.txt$", name)
        if m:
            out.setdefault(m.group(1), {"parts": {}, "n": 0})["body"] = name
    return out


def get_text(name):
    return ym("GetTextFile", what="ivr2:/AgentCode/" + name).get("contents") or ""


def pdf_text(data):
    pypdf = need("pypdf", "pypdf")
    r = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in r.pages)


# ---------- חלוקה למדורים והכנה להקראה ----------
def clean(t):
    t = re.sub(r"[‎‏‪-‮]", "", t)
    lines = []
    for ln in t.split("\n"):
        s = ln.strip()
        if not s or re.fullmatch(r"\d{1,2}", s):
            continue
        if re.fullmatch(r"\d{0,2}\s*עלון .{1,30}תש\S{1,4}", s) or re.fullmatch(r"בס['\"״׳]{1,2}ד.*", s):
            continue  # כותרת עמוד / בס"ד
        lines.append(re.sub(r"\s{2,}", " ", s))
    return lines


def parasha(t):
    m = re.search(r"עלון ([^\n]{1,25}?) תש", t)
    return m.group(1).strip() if m else ""


def split_sections(t):
    marks = []
    for ext, title, rx in SECTIONS:
        m = re.search(rx, t)
        if m:
            marks.append((m.start(), ext, title))
    marks.sort()
    out = {}
    for i, (pos, ext, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(t)
        body = " ".join(clean(t[pos:end]))
        if ext == "2":  # "היכן להשיג" - עד סוף הטקסט שלו בלבד
            body = body.split("תודה ויישר כח לכולם")[0] + " תודה ויישר כח לכולם על זיכוי הרבים."
        if len(body) > 80:
            out[ext] = (title, body)
    return out


ABBR = [  # ראשי תיבות נפוצים -> הקראה
    ("הקב\"ה", "הקדוש ברוך הוא"), ("י\"ל", "יש לומר"), ("וי\"ל", "ויש לומר"), ("ע\"פ", "על פי"),
    ("ע\"י", "על ידי"), ("עי\"ז", "על ידי זה"), ("א\"כ", "אם כן"), ("וא\"כ", "ואם כן"), ("אח\"כ", "אחר כך"),
    ("ע\"כ", "על כן"), ("ע\"ה", "עליו השלום"), ("ז\"ל", "זכרונו לברכה"),
    ("זצ\"ל", "זכר צדיק לברכה"), ("שליט\"א", "שליטא"), ("וכו'", "וכולי"), ("וגו'", "וגומר"),
    ("משא\"כ", "מה שאין כן"), ("כמו\"כ", "כמו כן"), ("וכ\"כ", "וכן כתב"), ("עכ\"ל", "עד כאן לשונו"),
    ("ב\"ה", "ברוך השם"), ("בע\"ה", "בעזרת השם"), ("אי\"ה", "אם ירצה השם"), ("רח\"ל", "רחמנא ליצלן"),
    ("גמ'", "גמרא"), ("רש\"י", "רשי"), ("פרש\"י", "פירש רשי"), ("תוס'", "תוספות"), ("שו\"ע", "שולחן ערוך"),
    ("משנ\"ב", "משנה ברורה"), ("ז\"א", "זאת אומרת"), ("כנ\"ל", "כנזכר לעיל"), ("וכדו'", "וכדומה"),
    ("ת\"ח", "תלמיד חכם"), ("ת\"ת", "תלמוד תורה"), ("יו\"ט", "יום טוב"), ("ר\"ה", "ראש השנה"),
]


def speakable(t):
    t = t.replace("''", "\"").replace("״", "\"").replace("׳", "'")
    for a, b in ABBR:
        t = re.sub(r"(?<![֐-׿])" + re.escape(a) + r"(?![֐-׿])", b, t)
    t = re.sub(r"\S+@\S+", "", t)  # כתובות מייל לא מוקראות
    return re.sub(r"\s{2,}", " ", t).strip()


def chunks(t, size=2500):
    out, cur = [], ""
    for s in re.split(r"(?<=[.?!:])\s+", t):
        if len(cur) + len(s) > size and cur:
            out.append(cur)
            cur = ""
        cur += (" " if cur else "") + s
    if cur:
        out.append(cur)
    return out


def render(text):
    """מקריא טקסט ארוך בחלקים ומחזיר נתיב mp3 מוקטן (מונו 16k), או זורק שגיאה."""
    edge_tts = need("edge_tts", "edge-tts")
    tmp = tempfile.gettempdir()
    raw = os.path.join(tmp, f"alon_{uuid.uuid4().hex}.mp3")
    with open(raw, "wb") as out:
        for part in chunks(text):
            p = os.path.join(tmp, f"alon_{uuid.uuid4().hex}.mp3")
            for attempt in range(3):
                try:
                    asyncio.run(asyncio.wait_for(edge_tts.Communicate(part, VOICE, rate=RATE).save(p), 240))
                    if os.path.getsize(p) > 1000:
                        break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        raise RuntimeError("tts failed: " + str(e)[:120])
                    time.sleep(5)
            out.write(open(p, "rb").read())
            os.remove(p)
    small = raw + ".small.mp3"
    c = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", raw, "-ac", "1", "-ar", "16000", "-b:a", "24k", small],
                       capture_output=True)
    os.remove(raw)
    if c.returncode or not os.path.exists(small):
        raise RuntimeError("ffmpeg failed: " + c.stderr.decode("utf-8", "replace")[:150])
    return small


def next_slot(folder):
    d = ym("GetIVR2Dir", path="ivr2:" + folder)
    nums = sorted(int(m.group(1)) for f in d.get("files") or [] if (m := re.match(r"^(\d{3})\.wav$", f.get("name", ""))))
    for n in nums[:max(0, len(nums) - (KEEP - 1))]:  # נשארים KEEP כולל החדש
        ym("FileAction", action="delete", what=f"ivr2:{folder}/{n:03d}.wav")
    return f"{(nums[-1] + 1) if nums else 100:03d}"


def upload(path, folder):
    slot = next_slot(folder)
    out = subprocess.run(["curl", "-sS", "-m", "1800", "-F", f"token={KEY}", "-F", f"path=ivr2:{folder}/{slot}.wav",
                          "-F", "convertAudio=1", "-F", f"file=@{path};filename={slot}.mp3", API + "UploadFile"],
                         capture_output=True, text=True).stdout
    os.remove(path)
    if '"OK"' not in out:
        raise RuntimeError("upload failed: " + out[:200])
    return slot


def process(iid, e, st):
    if e["n"]:
        if len(e["parts"]) < e["n"]:
            log(iid, f"waiting for parts {len(e['parts'])}/{e['n']}")
            return False
        b64 = "".join(get_text(e["parts"][i]) for i in range(1, e["n"] + 1))
        text = pdf_text(base64.b64decode(re.sub(r"\s", "", b64)))
    else:
        text = get_text(e["body"])
    name = parasha(text)
    secs = split_sections(text)
    log(iid, "parasha", name or "?", "sections", "".join(sorted(secs)))
    done = st["done"].setdefault(iid, [])
    for ext, (title, body) in sorted(secs.items(), key=lambda x: int(x[0]) or 10):
        if ext in done:
            continue
        intro = f"עלון לשבת{', פרשת ' + name if name else ''}. {title}. "
        try:
            mp3 = render(speakable(intro + body))
            slot = upload(mp3, f"/8/4/{ext}")
            done.append(ext)
            save(st)
            log(iid, f"uploaded {title} -> /8/4/{ext}/{slot}")
        except Exception as ex:  # noqa: BLE001
            log(iid, "FAILED", title, str(ex)[:200])
            return False
    for f in list(e["parts"].values()) + ([e["body"]] if e.get("body") else []):
        ym("FileAction", action="delete", what="ivr2:/AgentCode/" + f)
    log(iid, "done")
    return True


def in_window(now):
    wd, h = now.weekday(), now.hour  # 0=שני
    return (wd == 0 and h >= 17) or wd == 1 or (wd == 2 and h < 14)


def cycle(st):
    for iid, e in sorted(pending().items()):
        try:
            process(iid, e, st)
        except Exception as ex:  # noqa: BLE001
            log(iid, "error", str(ex)[:200])


if __name__ == "__main__":
    log("alon agent started")
    st = load()
    first = True
    while True:
        if first or in_window(datetime.now(IL)):
            try:
                cycle(st)
            except Exception as ex:  # noqa: BLE001
                log("cycle error", str(ex)[:200])
            first = False
        time.sleep(600)
