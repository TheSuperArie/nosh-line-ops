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
NIKUD = set(os.environ.get("ALON_NIKUD", "1").split(","))  # מדורים שמנוקדים אוטומטית לפני ההקראה (25/11: הגיגים)

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
        # אפשר להוסיף בסוף השורה: only=1,5 (רק המדורים האלה) replace=<מזהה ישן> (מוחק את הקבצים של העלון הישן במדורים האלה)
        m = re.match(r"^\s*(\d{4}-\d\d-\d\d_\d{4})\s+(\d+)((?:\s+\w+=\S+)*)\s*$", line)
        if not m or m.group(1) in st.get("finished", []):
            continue
        iid, n = m.group(1), int(m.group(2))
        if n:
            out[iid] = {"parts": {i: f"alon_{iid}_{i}of{n}.txt" for i in range(1, n + 1)}, "n": n}
        else:
            out[iid] = {"parts": {}, "n": 0, "body": f"alon_{iid}_body.txt"}
        opts = dict(o.split("=", 1) for o in m.group(3).split())
        if opts.get("only"):
            out[iid]["only"] = opts["only"].split(",")
        if opts.get("replace"):
            out[iid]["replace"] = opts["replace"]
    return out


def drop_issue(st, ext, old_iid):
    """מוחק מהשלוחה את הקבצים של עלון ישן (כשמעלים אותו מחדש אחרי תיקון)."""
    issues = st.setdefault("slots", {}).setdefault(ext, [])
    for item in [x for x in issues if x[0] == old_iid]:
        for sl in item[1]:
            try:
                ym("FileAction", action="delete", what=f"ivr2:/8/3/{ext}/{sl}.wav")
            except Exception:  # noqa: BLE001
                pass
        issues.remove(item)
        log(old_iid, f"replaced: deleted /8/3/{ext} {item[1]}")


def get_text(name):
    return ym("GetTextFile", what="ivr2:/AgentCode/" + name).get("contents") or ""


def pdf_text(data):
    pypdf = need("pypdf", "pypdf")
    r = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def joke_keys(data):
    """המילים המודגשות שפותחות כל הגיג (הגיג מתחיל בשורה שהקטע הראשון שלה מודגש).
    עוברים על הטקסט בסדר שבו הוא כתוב בקובץ (טור אחרי טור) - לא לפי גובה בעמוד, כי בעלון הרגיל
    יש 3 טורים באותו גובה (באג 25/11: כל ההגיגים התערבבו בין הטורים)."""
    pypdf = need("pypdf", "pypdf")
    keys, inside = [], False
    for page in pypdf.PdfReader(io.BytesIO(data)).pages:
        frags = []

        def v(text, cm, tm, fd, fs):
            if text:
                frags.append((tm[5] * cm[3] + cm[5], "Bold" in str((fd or {}).get("/BaseFont", "")), text))
        page.extract_text(visitor_text=v)
        lines, y0 = [], None
        for y, bold, text in frags:  # שורה חדשה = שינוי בגובה
            if y0 is None or abs(y - y0) > 2:
                lines.append([])
                y0 = y
            lines[-1].append((bold, text))
        for parts in lines:
            parts = [p for p in parts if p[1].strip()]
            if not parts:
                continue
            line = "".join(t for _, t in parts)
            if "הגיגים המיועדים" in line:
                inside = True
                continue
            if not inside:
                continue
            if "היכן ניתן להשיג" in line:
                return keys
            if parts[0][0]:
                k = ""
                for bold, t in parts:
                    if not bold:
                        break
                    k += t
                if len(re.sub(r"\W", "", k)) >= 2:
                    keys.append(k.strip())
    return keys if inside else []


def pdf_jokes(data, body):
    """מחלק את מדור ההגיגים (body = הטקסט הרגיל, שיוצא בסדר נכון) להגיגים, לפי המילים המודגשות. [] אם לא נמצאו."""
    body = re.sub(r"^.{0,10}הגיגים המיועדים[^\n]{0,40}?תועלת מזה\.?\s*", "", body)
    starts, pos = [], 0
    for k in joke_keys(data):
        rx = r"\s*".join(re.escape(c) for c in re.sub(r"\s", "", k))
        m = re.compile(rx).search(body, pos)
        if m:
            starts.append(m.start())
            pos = m.end()
    if len(starts) < 2:
        return []
    starts[0] = 0  # מה שלפני ההגיג הראשון (אם יש) מצטרף אליו
    jokes = [body[a:b].strip() for a, b in zip(starts, starts[1:] + [len(body)])]
    return [j for j in jokes if len(j) > 5]


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
    ("זצ\"ל", "זכר צדיק לברכה"), ("שליט\"א", "שְׁלִיטָא"), ("וכו'", "וכולי"), ("וגו'", "וגומר"),
    ("משא\"כ", "מה שאין כן"), ("כמו\"כ", "כמו כן"), ("וכ\"כ", "וכן כתב"), ("עכ\"ל", "עד כאן לשונו"),
    ("ב\"ה", "ברוך השם"), ("בע\"ה", "בעזרת השם"), ("אי\"ה", "אם ירצה השם"), ("רח\"ל", "רחמנא ליצלן"),
    ("גמ'", "גמרא"), ("רש\"י", "רשי"), ("פרש\"י", "פירש רשי"), ("תוס'", "תוספות"), ("שו\"ע", "שולחן ערוך"),
    ("משנ\"ב", "משנה ברורה"), ("ז\"א", "זאת אומרת"), ("כנ\"ל", "כנזכר לעיל"), ("וכדו'", "וכדומה"),
    ("המג\"ש", "המגיד שיעור"), ("מג\"ש", "מגיד שיעור"), ("הנ\"ל", "הנזכר לעיל"), ("ת\"ח", "תלמיד חכם"), ("ת\"ת", "תלמוד תורה"), ("יו\"ט", "יום טוב"), ("ר\"ה", "ראש השנה"),
]


def speakable(t):
    t = t.replace("''", "\"").replace("״", "\"").replace("׳", "'")
    for a, b in ABBR:
        t = re.sub(r"(?<![֐-׿])" + re.escape(a) + r"(?![֐-׿])", b, t)
    t = re.sub(r"\S+@\S+", "", t)  # כתובות מייל לא מוקראות
    # פיסוק: בלי רווח לפני סימן ("נאות ." -> "נאות."), רווח אחרי סימן שצמוד למילה, "---" = הפסקה
    t = re.sub(r"\s*-{2,}\s*", ". ", t)
    t = re.sub(r"\s+([.,?!:;])", r"\1", t)
    t = re.sub(r"([.,?!:;]+)(?=[א-תA-Za-z\"(])", r"\1 ", t)
    t = re.sub(r"([.,?!:;])\1+", r"\1", t.replace("...", "…")).replace("…", "...")
    t = re.sub(r"\s{2,}", " ", t).strip()
    if t and t[-1] not in ".?!:\"'":
        t += "."
    return t


NAKDAN = "https://nakdan-2-0.loadbalancer.dicta.org.il/api"  # הנקדן החינמי של דיקטה


def nikud(t):
    """מוסיף ניקוד מלא (נקדן דיקטה) כדי שהקריין יקרא נכון. אם נכשל - מחזיר את הטקסט כמו שהוא."""
    try:
        body = json.dumps({"task": "nakdan", "data": t, "genre": "modern", "addmorph": True, "keepnikud": True,
                           "keepqq": False, "nodageshdefmem": False, "patachma": False, "keepmetagim": True})
        r = subprocess.run(["curl", "-sS", "-m", "90", "-H", "Content-Type: text/plain;charset=UTF-8", "--data-binary", "@-", NAKDAN],
                           input=body.encode(), capture_output=True, timeout=120)
        out = ""
        for w in json.loads(r.stdout.decode("utf-8")):
            opts = w.get("options") or []
            out += w["word"] if w.get("sep") or not opts else (opts[0][0] if isinstance(opts[0], list) else opts[0]).replace("|", "")
        # דיקטה כותבת כתיב מלא ומסמנת ו/י שקטות במתג ("בֹּוֽקֶר", "מִיֽטָּה"). הקריין קורא אותן כעוד ו' ("שעווות"),
        # לכן מוחקים את האות השקטה עצמה (-> "בֹּקֶר", "מִטָּה"), ואז את שאר המתגים
        out = re.sub("[\u05d5\u05d9]\u05bd", "", out).replace("\u05bd", "")
        bare = lambda x: re.sub(r"[\u0591-\u05C7\s]", "", x)  # noqa: E731
        if len(bare(out)) < 0.9 * len(bare(t)):
            raise ValueError("short result")
        return out
    except Exception as e:  # noqa: BLE001
        log("nikud failed", str(e)[:120])
        return t


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
        if ext in done or ext in SKIP or (e.get("only") and ext not in e["only"]):
            continue
        if e.get("replace") and f"{iid}|{ext}" not in st.get("partial", {}):
            drop_issue(st, ext, e["replace"])
            save(st)
        intro = f"עלון לשבת{', פרשת ' + name if name else ''}. {title}. "
        try:
            items = [intro + body]
            if ext == JOKES and pdf:
                jokes = pdf_jokes(pdf, body)
                if len(jokes) > 1:
                    items = list(reversed(jokes))  # הראשון בעלון מקבל את המספר הגבוה = נשמע ראשון
            key = f"{iid}|{ext}"
            slots = st.setdefault("partial", {}).setdefault(key, [])  # התקדמות נשמרת אחרי כל קובץ
            for it in items[len(slots):]:
                txt = speakable(it)
                if ext in NIKUD:
                    txt = nikud(txt)
                slots.append(upload(render(txt), f"/8/3/{ext}"))
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
