"""הפטריוטים (ערוץ 14, יוטיוב) לשלוחה 2/1/3 - ישירות מהשרת, בלי GitHub.
כל 5 דקות (19:00-09:00) בודק את רשימת הסרטונים של הערוץ; פרק מלא חדש -> yt-dlp עם עוגיות (AgentCode/yt_cookies.txt)
-> ffmpeg ל-MP3 קטן -> העלאה לימות (שם קבוע: המספר הבא). מוריד לבד את yt-dlp, deno ו-ffmpeg לתיקייה bin."""
import base64, io, json, os, platform, re, stat, subprocess, tarfile, time, urllib.parse, urllib.request, zipfile
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(DIR, "bin")
STATE = os.path.join(DIR, "patriots_state.json")
IL = ZoneInfo("Asia/Jerusalem")
FOLDER, KEEP = "/2/1/3", 6
TEST = os.environ.get("PATRIOTS_TEST_TARGET", "ivr2:/AgentCode/test_patriots")  # בדיקה ראשונה (פעם אחת)
ARM = "aarch64" in platform.machine() or "arm" in platform.machine()


def log(*a):
    print(datetime.now(IL).strftime("%d/%m %H:%M:%S"), "[patriots]", *a, flush=True)


def ym(name, **p):
    p["token"] = KEY
    req = urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch(url):
    return subprocess.run(["curl", "-sSL", "-m", "600", url], capture_output=True).stdout


def status(st, msg):
    st.setdefault("log", []).append(datetime.now(IL).strftime("%d/%m %H:%M ") + msg)
    st["log"] = st["log"][-30:]
    try:
        ym("UploadTextFile", what="ivr2:/AgentCode/patriots_status.txt", contents="\n".join(st["log"]))
    except Exception:  # noqa: BLE001
        pass


def tools():
    os.makedirs(BIN, exist_ok=True)
    yd, dn, ff = (os.path.join(BIN, n) for n in ("yt-dlp", "deno", "ffmpeg"))
    if not os.path.exists(yd):
        open(yd, "wb").write(fetch("https://github.com/yt-dlp/yt-dlp/releases/latest/download/" + ("yt-dlp_linux_aarch64" if ARM else "yt-dlp_linux")))
    if not os.path.exists(dn):
        z = fetch("https://github.com/denoland/deno/releases/latest/download/deno-" + ("aarch64" if ARM else "x86_64") + "-unknown-linux-gnu.zip")
        zipfile.ZipFile(io.BytesIO(z)).extract("deno", BIN)
    if not os.path.exists(ff):
        t = fetch("https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-" + ("arm64" if ARM else "amd64") + "-static.tar.xz")
        with tarfile.open(fileobj=io.BytesIO(t), mode="r:xz") as tf:
            m = next(x for x in tf.getmembers() if x.name.endswith("/ffmpeg"))
            open(ff, "wb").write(tf.extractfile(m).read())
    for f in (yd, dn, ff):
        os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)
    return yd, ff


def cookies():
    q = urllib.parse.urlencode({"token": KEY, "path": "ivr2:/AgentCode/yt_cookies.txt"})
    d = urllib.request.urlopen(API + "DownloadFile?" + q, timeout=60).read()
    if d.startswith(b"B64:"):
        d = base64.b64decode(d[4:])
    path = os.path.join(DIR, "yt_cookies.txt")
    with open(path, "wb") as f:
        f.write(d)
    os.chmod(path, 0o600)
    return path


def episodes():
    x = fetch("https://www.youtube.com/feeds/videos.xml?channel_id=UCKEImtWikw9usC1pl_9m1nQ").decode("utf-8", "replace")
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
        v = re.search(r"<yt:videoId>([^<]+)", e)
        t = re.search(r"<title>([^<]+)", e)
        if v and t and "הפטריוטים" in t.group(1) and "המלאה" in t.group(1):
            out.append((v.group(1), t.group(1)))
    return out


def next_slot():
    d = ym("GetIVR2Dir", path="ivr2:" + FOLDER)
    nums = sorted(int(m.group(1)) for f in d.get("files") or [] if (m := re.match(r"^(\d{3})\.wav$", f.get("name", ""))))
    for n in nums[:max(0, len(nums) - (KEEP - 1))]:
        ym("FileAction", action="delete", what=f"ivr2:{FOLDER}/{n:03d}.wav")
    return f"ivr2:{FOLDER}/{(nums[-1] + 1) if nums else 100:03d}.wav"


def grab(vid, target):
    yd, ff = tools()
    env = dict(os.environ, PATH=BIN + ":" + os.environ.get("PATH", ""))
    raw, mp3 = os.path.join(DIR, "pat_raw"), os.path.join(DIR, "pat.mp3")
    for f in (raw, mp3):
        if os.path.exists(f):
            os.remove(f)
    r = subprocess.run([yd, "--cookies", cookies(), "-f", "bestaudio", "-o", raw, "--no-part", "--no-warnings",
                        f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True, env=env, timeout=3600)
    if r.returncode or not os.path.exists(raw):
        raise RuntimeError("yt-dlp: " + (r.stderr.strip()[-250:] or r.stdout.strip()[-250:]))
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", raw, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "24k", mp3], check=True, timeout=1800)
    os.remove(raw)
    out = subprocess.run(["curl", "-sS", "-m", "1800", "-F", f"token={KEY}", "-F", f"path={target}", "-F", "convertAudio=1",
                          "-F", f"file=@{mp3};filename=a.mp3", API + "UploadFile"], capture_output=True, text=True).stdout
    mb = os.path.getsize(mp3) / 1048576
    os.remove(mp3)
    if '"OK"' not in out:
        raise RuntimeError("upload: " + out[:200])
    return mb


def load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    log("patriots agent started")
    st = load()
    while True:
        try:
            now = datetime.now(IL)
            if not st.get("tested") and time.time() - st.get("test_at", 0) > 3600:  # בדיקת כל השרשרת (לתיקיית בדיקה) עד שמצליחה
                eps = episodes()
                if "seen" not in st:
                    st["seen"] = [v for v, _ in eps]
                st["test_at"] = time.time()
                if eps:
                    try:
                        mb = grab(eps[0][0], TEST + ".wav")
                        st["tested"] = True
                        status(st, f"בדיקה הצליחה: {eps[0][1][:60]} ({mb:.1f}MB)")
                    except Exception as e:  # noqa: BLE001
                        status(st, f"בדיקה נכשלה: {str(e)[:200]}")
                save(st)
            elif "seen" in st and (now.hour >= 19 or now.hour < 9):
                for vid, title in episodes():
                    if vid in st["seen"]:
                        continue
                    try:
                        mb = grab(vid, next_slot())
                        st["seen"].append(vid)
                        status(st, f"עלה: {title[:70]} ({mb:.1f}MB)")
                    except Exception as e:  # noqa: BLE001
                        n = st.setdefault("fails", {}).get(vid, 0) + 1
                        st["fails"][vid] = n
                        status(st, f"נכשל ({n}) {vid}: {str(e)[:200]}")
                        if n >= 6:  # לא מנסים לנצח
                            st["seen"].append(vid)
                    break  # אחד בכל סבב
                st["seen"] = st["seen"][-60:]
                save(st)
        except Exception as e:  # noqa: BLE001
            log("error", str(e)[:200])
        time.sleep(300)
