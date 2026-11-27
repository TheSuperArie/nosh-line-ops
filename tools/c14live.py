"""הקלטה מהשידור החי של ערוץ 14 ביוטיוב (27/11): רצועת 19:00-20:00 (שרון גל - מוצ"ש/א'/ב'/ג', איילה חסון - ד'/ה').
ערוץ 14 לא מעלה את התוכנית המלאה ליוטיוב (רק קטעים), והאתר שלו מאחורי Cloudflare - לכן מקליטים מהשידור החי (24/7).
מופעל מהשרת (shows_agent, LIVE_REC) כ-20 דקות לפני; מחכה לשעת ההתחלה, מקליט שמע עד הסוף, ומעלה לתיקייה במספר הבא.
env: START/END (HH:MM שעון ישראל), FOLDER, KEEP, LABEL, TEST=1 (הקלטה קצרה ל-AgentCode, MINUTES דקות)."""
import json, os, re, subprocess, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IL = ZoneInfo("Asia/Jerusalem")
API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ["YM_TOKEN"].strip()
FOLDER = (os.environ.get("FOLDER") or "/2/2").strip()
KEEP = int(os.environ.get("KEEP") or 20)
LABEL = (os.environ.get("LABEL") or "C14_19").strip()
TEST = os.environ.get("TEST") == "1"
CHANNEL = "https://www.youtube.com/@c14news"
LEAD, TAIL = 20, 75  # שניות: מתחילים מעט לפני, וממשיכים קצת אחרי (השידור ביוטיוב מאחר בכ-20-40 שנ')
DONE = "ivr2:/AgentCode/c14live_done.json"


def ym(name, **p):
    p["token"] = KEY
    with urllib.request.urlopen(urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode()), timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def note(msg, kind="notice"):
    print(f"::{kind} title={LABEL}::{msg}", flush=True)


def at(hm, base):
    h, m = map(int, hm.split(":"))
    return base.replace(hour=h, minute=m, second=0, microsecond=0)


now = datetime.now(IL)
if TEST:
    t0 = now
    t1 = now + timedelta(minutes=int(os.environ.get("MINUTES") or 2))
else:
    t0 = at(os.environ.get("START") or "19:00", now) - timedelta(seconds=LEAD)
    t1 = at(os.environ.get("END") or "20:00", now) + timedelta(seconds=TAIL)
    if now > t1 - timedelta(minutes=20):
        note(f"מאוחר מדי ({now:%H:%M}) - לא מקליט", "warning")
        sys.exit(0)
day = t0.strftime("%Y-%m-%d")
try:
    done = json.loads(urllib.request.urlopen(API + "DownloadFile?" + urllib.parse.urlencode({"token": KEY, "path": DONE}),
                                             timeout=60).read().decode() or "[]")
except Exception:  # noqa: BLE001
    done = []
if day in done and not TEST:
    note(f"{day} כבר הוקלט")
    sys.exit(0)


def live_id():
    """מזהה השידור החי הקבוע של הערוץ ("עכשיו 14 ... שידור חי"), ואם אין - כל שידור חי שרץ עכשיו."""
    r = subprocess.run(["yt-dlp", "--flat-playlist", "--playlist-end", "15", "--print", "%(id)s\t%(live_status)s\t%(title)s",
                        CHANNEL + "/streams"], capture_output=True, text=True, timeout=180)
    rows = [l.split("\t") for l in r.stdout.splitlines() if l.count("\t") >= 2]
    lives = [x for x in rows if x[1] == "is_live"]
    for x in lives:
        if "עכשיו 14" in x[2]:
            return x[0]
    return lives[0][0] if lives else "SBbgmLLEhhk"


def manifest(vid):
    """כתובת HLS של השידור החי (פורמט נמוך עם שמע - מספיק לטלפון). קודם עם עוגיות, ואם נחסם - בלי."""
    for extra in (["--cookies", "/tmp/cookies.txt"], ["--cookies", "/tmp/cookies.txt", "--extractor-args", "youtube:player_client=web_safari"],
                  [], ["--extractor-args", "youtube:player_client=tv"]):
        r = subprocess.run(["yt-dlp", *extra, "-g", "-f", "91/92/93/94/bestaudio/best", "--no-warnings",
                            f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True, timeout=180)
        u = (r.stdout.strip().splitlines() or [""])[0]
        if r.returncode == 0 and u.startswith("http"):
            return u
        note(f"manifest {extra[-1:] or 'plain'}: {(r.stderr or '').strip()[-200:]}", "warning")
    return None


vid = live_id()
note(f"שידור חי {vid}; הקלטה {t0:%H:%M:%S}-{t1:%H:%M:%S}")
url = manifest(vid)  # מכינים מראש, ומרעננים לפני ההתחלה
wait = (t0 - datetime.now(IL)).total_seconds()
if wait > 0:
    print(f"waiting {wait:.0f}s", flush=True)
    time.sleep(max(0, wait - 90))
    url = manifest(vid) or url
    time.sleep(max(0, (t0 - datetime.now(IL)).total_seconds()))
parts, fails = [], 0
while (left := (t1 - datetime.now(IL)).total_seconds()) > 15 and fails < 30:
    if not url:
        url = manifest(vid)
        if not url:
            fails += 1
            time.sleep(20)
            continue
    p = f"/tmp/part{len(parts):02d}.mp3"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
                        "-i", url, "-t", str(int(left)), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "24k", p],
                       capture_output=True, text=True, timeout=int(left) + 300)
    got = os.path.getsize(p) if os.path.exists(p) else 0
    if got > 20000:
        parts.append(p)
    left2 = (t1 - datetime.now(IL)).total_seconds()
    if left2 > 15:  # ffmpeg נעצר לפני הזמן (הכתובת פגה / ניתוק) - כתובת חדשה וממשיכים לקטע הבא
        fails += 1
        note(f"נקטע ב-{datetime.now(IL):%H:%M:%S} (נשארו {left2:.0f} שנ'): {r.stderr.strip()[-200:]}", "warning")
        url = manifest(vid)
if not parts:
    note("לא הוקלט כלום", "error")
    sys.exit(1)
with open("/tmp/list.txt", "w") as f:
    f.writelines(f"file '{p}'\n" for p in parts)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "/tmp/list.txt", "-c", "copy", "/tmp/p.mp3"], check=True)
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", "/tmp/p.mp3"],
                           capture_output=True, text=True).stdout.strip() or 0)
note(f"הוקלטו {dur / 60:.1f} דק' ב-{len(parts)} חלקים, {os.path.getsize('/tmp/p.mp3') / 1048576:.1f}MB")
if not TEST and dur < 30 * 60:
    note(f"קצר מדי ({dur / 60:.1f} דק') - בכל זאת מעלה", "warning")
folder = "/AgentCode" if TEST else FOLDER
d = ym("GetIVR2Dir", path="ivr2:" + folder)
nums = sorted(int(m.group(1)) for f in d.get("files") or [] if (m := re.match(r"^(\d{3})\.wav$", f.get("name", ""))))
for n in ([] if TEST else nums[:max(0, len(nums) - (KEEP - 1))]):
    ym("FileAction", action="delete", what=f"ivr2:{folder}/{n:03d}.wav")
slot = "test_c14live" if TEST else f"{(nums[-1] + 1) if nums else 100:03d}"
out = subprocess.run(["curl", "-sS", "-m", "1800", "-F", f"token={KEY}", "-F", f"path=ivr2:{folder}/{slot}.wav", "-F", "convertAudio=1",
                      "-F", "file=@/tmp/p.mp3;filename=p.mp3", API + "UploadFile"], capture_output=True, text=True).stdout
note(f"upload -> {folder}/{slot}: {out[:150]}")
if '"OK"' not in out:
    sys.exit(1)
if not TEST:
    ym("UploadTextFile", what=DONE, contents=json.dumps((done + [day])[-30:]))
