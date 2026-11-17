#!/usr/bin/env bash
# התקנת מנהל הסוכנים של קו נווה שלום (מבזקים לשלוחה 1, ובהמשך התוכניות) כשירות רקע.
# מריצים פעם אחת:   sudo bash install_nosh_line.sh
# הקוד של הסוכנים נמשך אוטומטית ממערכת ימות המשיח של הקו - עדכונים מגיעים לבד.
# משאבים: מוגבל ל-20% מעבד ו-400MB זיכרון (בפועל כ-30MB). לא רץ בשבת ויום טוב.
set -euo pipefail
DIR=/opt/nosh-line
id nosh >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin nosh
mkdir -p "$DIR"
cat > "$DIR/bootstrap.py" <<'NOSH_EOF'
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
NOSH_EOF
if ! python3 -c "import venv, ensurepip" 2>/dev/null; then (apt-get update -qq && apt-get install -y -qq python3-venv) || (dnf install -y python3 >/dev/null); fi
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q -U pip wheel setuptools
"$DIR/venv/bin/pip" install -q zmanim
"$DIR/venv/bin/python" -c "import zoneinfo" 2>/dev/null || "$DIR/venv/bin/pip" install -q backports.zoneinfo tzdata
"$DIR/venv/bin/python" -c "import zmanim; from zoneinfo import ZoneInfo" 2>/dev/null || "$DIR/venv/bin/python" -c "import zmanim; from backports.zoneinfo import ZoneInfo; ZoneInfo('Asia/Jerusalem'); print('python deps OK')"
if [ -n "${YM_TOKEN:-}" ]; then printf 'YM_TOKEN=%s\n' "$YM_TOKEN" > "$DIR/env"; fi
if [ ! -s "$DIR/env" ]; then
  read -rsp "הדבק את מפתח ה-API של ימות המשיח (לא יוצג על המסך): " K; echo
  printf 'YM_TOKEN=%s\n' "$K" > "$DIR/env"
fi
chmod 600 "$DIR/env"; chown -R nosh:nosh "$DIR"
cat > /etc/systemd/system/nosh-line.service <<'UNIT'
[Unit]
Description=Nosh phone line agents (Yemot)
After=network-online.target
Wants=network-online.target
[Service]
User=nosh
WorkingDirectory=/opt/nosh-line
EnvironmentFile=/opt/nosh-line/env
Environment=NEWS_STATE=/opt/nosh-line/news_state.json
ExecStart=/opt/nosh-line/venv/bin/python /opt/nosh-line/bootstrap.py
Restart=always
RestartSec=30
MemoryMax=400M
CPUQuota=20%
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now nosh-line
sleep 20
journalctl -u nosh-line --no-pager -n 8 || true
echo
echo "הותקן ✓   לוג: journalctl -u nosh-line -f   |   עצירה: systemctl stop nosh-line"
echo "הסרה מלאה: systemctl disable --now nosh-line && rm -rf /opt/nosh-line /etc/systemd/system/nosh-line.service && userdel nosh"
