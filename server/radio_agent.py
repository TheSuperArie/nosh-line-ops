"""סוכן הרדיו לקו נווה שלום - רץ בשרת (בלי GitHub): בודק כל 5 דקות אם עלתה תוכנית מלאה חדשה,
מוריד את ה-MP3 ישירות מהאתר ומעלה לשלוחה (ימות ממיר בעצמו). שמות קבצים קבועים: החדש מקבל את המספר הגבוה.
פתחי וזמרי (גלי ישראל) א'-ה' -> 2/1/1 | ינון מגל ובן כספית (103) ב'-ד' -> 2/1/2 | מחשבות בע"מ (גלצ) ו' -> 2/2"""
import html, json, os, re, subprocess, time, urllib.parse, urllib.request
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "radio_state.json")
TMP = os.path.join(DIR, "radio_tmp.mp3")
IL = ZoneInfo("Asia/Jerusalem")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
GLZ = ("https://www.omnycontent.com/d/playlist/6dcbc33f-1fb6-49de-9ae2-ad8a00c01523/"
       "71453d31-14da-43ed-ae4a-adef00df863c/755c5b83-44d1-4a8c-b88f-adef00e16066/podcast.rss")
# יום: 0=שני ... 4=שישי 6=ראשון (weekday של פייתון)
SHOWS = [  # (שם, ימים, שעות, תיקייה, כמה לשמור)
    ("gly", (6, 0, 1, 2, 3), (9, 20), "/2/1/1", 6),
    ("103", (0, 1, 2), (9, 20), "/2/1/2", 6),
    ("glz", (4,), (7, 15), "/2/2", 15),
]


STATUS = []


def log(*a):
    line = datetime.now(IL).strftime("%d/%m %H:%M") + " " + " ".join(str(x) for x in a)
    print("[radio]", line, flush=True)
    STATUS.append(line)
    del STATUS[:-60]
    try:
        ym("UploadTextFile", what="ivr2:/AgentCode/radio_status.txt", contents="\n".join(STATUS))
    except Exception:  # noqa: BLE001
        pass


def get(url):
    r = subprocess.run(["curl", "-sSL", "-m", "60", "-A", UA, url], capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200])
    return r.stdout.decode("utf-8", "replace")


def ym(name, **p):
    p["token"] = KEY
    req = urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------- מקורות: מחזירים [(מזהה, כותרת, קישור mp3)] מהחדש לישן ----------
def rss(url, need_full):
    out = []
    for it in re.findall(r"<item>(.*?)</item>", get(url), re.S)[:15]:
        m = re.search(r"<title>(.*?)</title>", it, re.S)
        t = re.sub(r"^<!\[CDATA\[|\]\]>$", "", html.unescape(m.group(1)).strip()) if m else ""
        enc = re.search(r'<enclosure[^>]*url="([^"]+)"', it)
        if t and enc and (not need_full or "המלאה" in t):
            out.append((t, t, html.unescape(enc.group(1))))
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
                if it.get("isFullProgram") and it.get("item_stream_url"):
                    out.append((str(it.get("itemId")), it.get("itemTitle") or "", it["item_stream_url"]))
    return out


SRC = {
    "gly": gly,
    "103": lambda: rss("https://103fm.maariv.co.il/rss/mediarss.aspx?programId=262", True),
    "glz": lambda: rss(GLZ, False),
}


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"seen": {}}


def save(st):
    json.dump(st, open(STATE + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(STATE + ".tmp", STATE)


def next_slot(folder, keep):
    d = ym("GetIVR2Dir", path="ivr2:" + folder)
    nums = sorted(int(m.group(1)) for f in d.get("files") or [] if (m := re.match(r"^(\d{3})\.wav$", f.get("name", ""))))
    for n in nums[:max(0, len(nums) - (keep - 1))]:
        ym("FileAction", action="delete", what=f"ivr2:{folder}/{n:03d}.wav")
    return f"{(nums[-1] + 1) if nums else 100:03d}"


def upload(url, folder, keep, title):
    r = subprocess.run(["curl", "-sSL", "-m", "1800", "-A", UA, "-o", TMP, url], capture_output=True)
    if r.returncode or not os.path.exists(TMP) or os.path.getsize(TMP) < 500000:
        raise RuntimeError("download failed: " + r.stderr.decode("utf-8", "replace")[:150])
    mb = os.path.getsize(TMP) / 1048576
    slot = next_slot(folder, keep)
    out = subprocess.run(["curl", "-sS", "-m", "3600", "-F", f"token={KEY}", "-F", f"path=ivr2:{folder}/{slot}.wav",
                          "-F", "convertAudio=1", "-F", f"file=@{TMP};filename={slot}.mp3", API + "UploadFile"],
                         capture_output=True, text=True).stdout
    os.remove(TMP)
    if '"OK"' not in out:
        raise RuntimeError("upload failed: " + out[:200])
    log(f"uploaded {title[:60]} -> {folder}/{slot} ({mb:.0f}MB)")


def cycle(st):
    now = datetime.now(IL)
    for name, days, (h1, h2), folder, keep in SHOWS:
        if now.weekday() not in days or not (h1 <= now.hour < h2):
            continue
        try:
            items = SRC[name]()
        except Exception as e:  # noqa: BLE001
            log("check failed", name, str(e)[:150])
            continue
        seen = st["seen"].get(name)
        st["seen"][name] = list(dict.fromkeys((seen or []) + [i[0] for i in items]))[-80:]
        if seen is None:
            log("first look", name, len(items), "(not uploading old ones)")
            save(st)
            continue
        for iid, title, url in [i for i in items if i[0] not in seen][:1]:  # רק החדש ביותר
            log("new", name, title[:50], "- downloading")
            try:
                upload(url, folder, keep, title)
            except Exception as e:  # noqa: BLE001
                log("FAILED", name, title[:50], str(e)[:200])
                st["seen"][name].remove(iid)  # ננסה שוב בסבב הבא
        save(st)


if __name__ == "__main__":
    log("radio agent started")
    st = load()
    while True:
        try:
            cycle(st)
        except Exception as e:  # noqa: BLE001
            log("cycle error", str(e)[:200])
        time.sleep(300)
