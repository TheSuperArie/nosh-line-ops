"""סוכן "עלון לשבת" (קולדצקי) לקו נווה שלום - רץ בשרת.
סקריפט של גוגל בחשבון של אריה מעלה את ה-PDF של העלון לתיקייה AgentCode בימות (בחלקים base64:
alon_<תאריך>_<i>of<n>.txt). הסוכן מחבר, מוציא טקסט (pypdf), מחלק למדורים לפי הכותרות הקבועות,
מקריא כל מדור (edge-tts, אותו קול כמו בקו) ומעלה לשלוחה 8/3/<מספר>. בכל שלוחה נשמרים 2 עלונים אחרונים.
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
RATE = os.environ.get("ALON_RATE", "+0%")  # מהירות רגילה (לא כמו המבזקים) - ביקש 19/11
KEEP = 2  # כמה עלונים נשמרים בכל שלוחה

# (שלוחה, שם להקראה, ביטוי שמזהה את תחילת המדור בטקסט) - הסדר נקבע ע"י אריה 18/11
SECTIONS = [
    ("1", "הגיגים", r"הגיגים המיועדים"),
    ("2", "היכן ניתן להשיג את העלון", r"היכן ניתן להשיג את העלון"),
    ("3", "התורה חסה", r"התורה חסה\s*:"),
    ("4", "רעיונות מהקוראים", r"מדור זה יש להימנע מלקרוא|רעיונות שנשלחו השבוע"),
    ("5", "וורטים על פרשת השבוע", r"וו?ארטים (?:על |ל)פרשת"),
    ("6", "חידות", r"(?m)^[ \t]*חידות[ \t]*$|חידון שינון הפרשה"),
    ("7", "ידיעות חשובות ודברים מגדולי ישראל", r"ידיעות חשובות"),
    ("8", "מאמרי מוסר וסיפורים", r"מאמרי מוסר"),
    ("9", "הלכות", r"(?m)^[ \t]*הלכות(?:[ \t]+הקשורות[^\n]{0,25})?[ \t]*$"),
    ("0", "שאלות גמרא פירוש רש\"י ותוספות", r"שאלות גפ[\"״'׳]{1,2}ת"),
]
SKIP = {"0"}  # שאלות גפ"ת - לא עולה (ביקש 19/11); נשאר ברשימה רק כדי לסמן איפה נגמרות החידות
JOKES = "1"   # הגיגים - כל הגיג בהודעה נפרדת

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
def pending(st):
    """הסקריפט של גוגל רושם כל עלון בשורה ב-AgentCode/alon_index.txt: "<מזהה> <מספר חלקים>" (0 = טקסט מהמייל).
    (GetIVR2Dir לא מציג קבצי txt, לכן צריך אינדקס.)"""
    out = {}
    idx = ym("GetTextFile", what="ivr2:/AgentCode/alon_index.txt").get("contents") or ""
    for line in idx.splitlines():
        m = re.match(r"^\s*(\d{4}-\d\d-\d\d_\d{4})\s+(\d+)\s*$", line)
        if not m or m.group(1) in st.get("finished", []):
            continue
        iid, n = m.group(1), int(m.group(2))
        if n:
            out[iid] = {"parts": {i: f"alon_{iid}_{i}of{n}.txt" for i in range(1, n + 1)}, "n": n}
        else:
            out[iid] = {"parts": {}, "n": 0, "body": f"alon_{iid}_body.txt"}
    return out


def get_text(name):
    return ym("GetTextFile", what="ivr2:/AgentCode/" + name).get("contents") or ""


def pdf_text(data):
    pypdf = need("pypdf", "pypdf")
    r = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def pdf_jokes(data):
    """ההגיגים: כל הגיג מתחיל בשורה שהמילים הראשונות שלה מודגשות. מחזיר רשימת הגיגים (או [] אם לא נמצאו)."""
    pypdf = need("pypdf", "pypdf")
    jokes, inside = [], False
    for page in pypdf.PdfReader(io.BytesIO(data)).pages:
        rows = {}

        def v(text, cm, tm, fd, fs):
            if text.strip():
                y = round(tm[5] * cm[3] + cm[5], 1)
                rows.setdefault(y, []).append(("Bold" in str((fd or {}).get("/BaseFont", "")), text))
        page.extract_text(visitor_text=v)
        for y in sorted(rows, reverse=True):
            parts = rows[y]
            line = " ".join(t.strip() for _, t in parts).strip()
            if "הגיגים המיועדים" in line:
                inside = True
                continue
            if not inside:
                continue
            if "היכן ניתן להשיג" in line:
                return [j for j in jokes if len(j) > 5]
            if re.fullmatch(r"\d{0,2}\s*עלון .{1,30}תש\S{1,4}", line) or re.fullmatch(r"\d{1,2}", line):
                continue  # כותרת/מספר עמוד
            if parts[0][0] or not jokes:
                jokes.append(line)
            else:
                jokes[-1] += " " + line
    return [j for j in jokes if len(j) > 5] if inside else []


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
    for rx in (r"פרשת ([א-ת]+(?: [א-ת]+)?) תש", r"עלון ([א-ת]+(?: [א-ת]+)?) תש"):
        m = re.search(rx, t)
        if m:
            return m.group(1).strip()
    return ""


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
            for attempt in range(6):  # השירות של מיקרוסופט לפעמים מגביל זמנית ("No audio was received")
                try:
                    asyncio.run(asyncio.wait_for(edge_tts.Communicate(part, VOICE, rate=RATE).save(p), 240))
                    if os.path.getsize(p) > 1000:
                        break
                except Exception as e:  # noqa: BLE001
                    if attempt == 5:
                        raise RuntimeError("tts failed: " + str(e)[:120])
                    time.sleep(15 * (attempt + 1))
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
    return f"{(nums[-1] + 1) if nums else 100:03d}"


def prune(st, ext):
    """בכל שלוחה נשארים רק הקבצים של KEEP העלונים האחרונים (הגיגים = כמה קבצים לעלון)."""
    issues = st.setdefault("slots", {}).setdefault(ext, [])
    while len(issues) > KEEP:
        old_iid, slots = issues.pop(0)
        for sl in slots:
            try:
                ym("FileAction", action="delete", what=f"ivr2:/8/3/{ext}/{sl}.wav")
            except Exception:  # noqa: BLE001
                pass
        log(old_iid, f"deleted old issue from /8/3/{ext}: {slots}")


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
        b64 = "".join(get_text(e["parts"][i]) for i in range(1, e["n"] + 1))
        pdf = base64.b64decode(re.sub(r"\s", "", b64))
        text = pdf_text(pdf)
    else:
        pdf = None
        text = get_text(e["body"])
    name = parasha(text)
    secs = split_sections(text)
    log(iid, "parasha", name or "?", "sections", "".join(sorted(secs)))
    done = st["done"].setdefault(iid, [])
    for ext, (title, body) in sorted(secs.items(), key=lambda x: int(x[0]) or 10):
        if ext in done or ext in SKIP:
            continue
        intro = f"עלון לשבת{', פרשת ' + name if name else ''}. {title}. "
        try:
            items = [intro + body]
            if ext == JOKES and pdf:
                jokes = pdf_jokes(pdf)
                if len(jokes) > 1:
                    items = list(reversed(jokes))  # הראשון בעלון מקבל את המספר הגבוה = נשמע ראשון
            key = f"{iid}|{ext}"
            slots = st.setdefault("partial", {}).setdefault(key, [])  # התקדמות נשמרת אחרי כל קובץ
            for it in items[len(slots):]:
                slots.append(upload(render(speakable(it)), f"/8/3/{ext}"))
                save(st)
                time.sleep(2)
            st["partial"].pop(key, None)
            st.setdefault("slots", {}).setdefault(ext, []).append([iid, slots])
            prune(st, ext)
            done.append(ext)
            save(st)
            log(iid, f"uploaded {title} -> /8/3/{ext} {slots[0]}..{slots[-1]} ({len(slots)})")
        except Exception as ex:  # noqa: BLE001
            log(iid, "FAILED", title, str(ex)[:200])
            return False
    for f in list(e["parts"].values()) + ([e["body"]] if e.get("body") else []):
        ym("FileAction", action="delete", what="ivr2:/AgentCode/" + f)
    st.setdefault("finished", []).append(iid)
    st["finished"] = st["finished"][-30:]
    save(st)
    log(iid, "done")
    return True


def in_window(now):
    wd, h = now.weekday(), now.hour  # 0=שני
    return (wd == 0 and h >= 17) or wd == 1 or (wd == 2 and h < 14)


def cycle(st):
    """מחזיר True אם נשארה עבודה (כישלון) - אז ממשיכים לנסות כל 10 דקות גם מחוץ לחלון."""
    left = False
    for iid, e in sorted(pending(st).items()):
        try:
            left |= not process(iid, e, st)
        except Exception as ex:  # noqa: BLE001
            left = True
            log(iid, "error", str(ex)[:200])
    return left


if __name__ == "__main__":
    log("alon agent started")
    st = load()
    again = True
    while True:
        if again or in_window(datetime.now(IL)):
            try:
                again = cycle(st)
            except Exception as ex:  # noqa: BLE001
                log("cycle error", str(ex)[:200])
                again = True
        time.sleep(600)
