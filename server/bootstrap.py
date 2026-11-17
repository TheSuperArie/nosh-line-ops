"""מנהל סוכני הקו בשרת. מוריד את קוד הסוכנים מתיקיית AgentCode במערכת ימות המשיח (כך שעדכונים
מגיעים לבד, בלי לגעת בשרת), מריץ כל סוכן כתהליך נפרד ומפעיל מחדש כשהקוד משתנה.
בשבת ויום טוב - עוצר את כל הסוכנים ולא עושה כלום עד צאת השבת."""
import base64, hashlib, json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8 (אובונטו 20.04)
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
IL = ZoneInfo("Asia/Jerusalem")
procs = {}

def log(*a):
    print(datetime.now(IL).strftime("%d/%m %H:%M:%S"), "[boot]", *a, flush=True)

def shabbat_ok():
    from zmanim.util.geo_location import GeoLocation
    from zmanim.zmanim_calendar import ZmanimCalendar
    from zmanim.hebrew_calendar.jewish_calendar import JewishCalendar
    loc = GeoLocation("Jerusalem", 31.778, 35.235, "Asia/Jerusalem", elevation=754)
    now = datetime.now(IL)
    d = now.date()
    sunset = lambda x: ZmanimCalendar(geo_location=loc, date=x).sunset()
    assur = lambda x: JewishCalendar(x, in_israel=True).is_assur_bemelacha()
    if assur(d) and now < sunset(d) + timedelta(minutes=75):
        return False
    if assur(d + timedelta(days=1)) and now >= sunset(d) - timedelta(minutes=45):
        return False
    return True

def download(name):
    q = urllib.parse.urlencode({"token": KEY, "path": f"ivr2:/AgentCode/{name}"})
    with urllib.request.urlopen(API + "DownloadFile?" + q, timeout=60) as r:
        data = r.read()
    if data.lstrip().startswith(b'{"yemotAPIVersion'):  # ימות מחזיר שגיאה כ-JSON (למשל מפתח שגוי)
        raise RuntimeError("ימות: " + data.decode("utf-8", "replace")[:200])
    if data.startswith(b"B64:"):  # ימות מוחק לוכסנים הפוכים - לכן הקוד נשמר שם בבסיס 64
        data = base64.b64decode(data[4:])
    return data

def update():
    """מוריד את רשימת הסוכנים ואת הקוד שלהם. מחזיר את שמות הסוכנים שהשתנו."""
    try:  # עדכון עצמי של מנהל הסוכנים
        me = download("bootstrap.txt")
        path = os.path.abspath(__file__)
        if me.strip().startswith(b'"""') and me != open(path, "rb").read():
            open(path, "wb").write(me)
            log("bootstrap updated - restarting")
            for n in list(procs):
                stop(n)
            os.execv(sys.executable, [sys.executable, path])
    except urllib.error.HTTPError:
        pass
    manifest = [l.strip() for l in download("manifest.txt").decode("utf-8").splitlines() if l.strip() and not l.startswith("#")]
    changed = []
    for name in manifest:  # news_agent -> AgentCode/news_agent.txt
        code = download(f"{name}.txt")
        if not code.strip().startswith((b'"""', b"import", b"#")):
            log("skip suspicious file", name)
            continue
        path = os.path.join(DIR, f"{name}.py")
        old = open(path, "rb").read() if os.path.exists(path) else b""
        if hashlib.sha256(old).digest() != hashlib.sha256(code).digest():
            open(path, "wb").write(code)
            changed.append(name)
    for name in list(procs):
        if name not in manifest:
            stop(name)
    return manifest, changed

def start(name):
    procs[name] = subprocess.Popen([sys.executable, os.path.join(DIR, f"{name}.py")], cwd=DIR)
    log("started", name)

def stop(name):
    p = procs.pop(name, None)
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()
        log("stopped", name)

if __name__ == "__main__":
    if not KEY:
        sys.exit("YM_TOKEN חסר")
    log("bootstrap started")
    last_check = 0
    manifest = []
    while True:
        try:
            if not shabbat_ok():
                for n in list(procs):
                    stop(n)
                time.sleep(120)
                continue
            if time.time() - last_check > 15 * 60 or not manifest:
                manifest, changed = update()
                last_check = time.time()
                for n in changed:
                    log("code updated:", n)
                    stop(n)
            for n in manifest:
                p = procs.get(n)
                if p is None or p.poll() is not None:
                    start(n)
        except Exception as e:  # noqa
            log("error:", str(e)[:200])
        time.sleep(60)
