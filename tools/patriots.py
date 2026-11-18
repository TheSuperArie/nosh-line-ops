"""מוריד פרק מלא של הפטריוטים מיוטיוב (עוגיות + PO token) ומעלה לשלוחה 2/1/3 בשם קבוע (המספר הבא)."""
import json, os, re, subprocess, sys, urllib.parse, urllib.request
API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ["YM_TOKEN"].strip()
FOLDER, KEEP, DONE = "/2/1/3", 6, "ivr2:/AgentCode/patriots_done.json"


def ym(name, **p):
    p["token"] = KEY
    with urllib.request.urlopen(urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode()), timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def done_list():
    try:
        q = urllib.parse.urlencode({"token": KEY, "path": DONE})
        return json.loads(urllib.request.urlopen(API + "DownloadFile?" + q, timeout=60).read().decode() or "[]")
    except Exception:  # noqa: BLE001
        return []


vid = (os.environ.get("VID") or "").strip()
if not vid:  # בלי מזהה - הפרק המלא האחרון מהערוץ
    x = urllib.request.urlopen("https://www.youtube.com/feeds/videos.xml?channel_id=UCKEImtWikw9usC1pl_9m1nQ", timeout=30).read().decode()
    for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
        t = re.search(r"<title>([^<]+)", e).group(1)
        if "הפטריוטים" in t and "המלאה" in t:
            vid = re.search(r"<yt:videoId>([^<]+)", e).group(1)
            break
done = done_list()
TEST = os.environ.get("TEST") == "1"  # בדיקה: מעלה לתיקיית AgentCode ולא מסמן כבוצע
if not vid or (vid in done and not TEST):
    print(f"::notice title=PATRIOTS::nothing to do ({vid or 'no episode'})")
    sys.exit(0)
ok = False
for client in (None, "web_safari", "mweb", "tv"):
    extra = ["--extractor-args", f"youtube:player_client={client}"] if client else []
    r = subprocess.run(["yt-dlp"] + extra + ["--cookies", "/tmp/cookies.txt", "-f", "bestaudio/best", "-o", "/tmp/raw",
                        "--no-part", "--no-warnings", f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists("/tmp/raw"):
        ok = True
        print(f"::notice title=PATRIOTS client::{client or 'default'}")
        break
    print(f"::warning title=PATRIOTS {client or 'default'}::{(r.stderr or r.stdout).strip()[-300:]}")
if not ok:
    sys.exit(1)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "/tmp/raw", "-vn", "-ac", "1", "-ar", "16000", "-b:a", "24k", "/tmp/p.mp3"], check=True)
if TEST:
    FOLDER = "/AgentCode"
d = ym("GetIVR2Dir", path="ivr2:" + FOLDER)
nums = sorted(int(m.group(1)) for f in d.get("files") or [] if (m := re.match(r"^(\d{3})\.wav$", f.get("name", ""))))
for n in ([] if TEST else nums[:max(0, len(nums) - (KEEP - 1))]):
    ym("FileAction", action="delete", what=f"ivr2:{FOLDER}/{n:03d}.wav")
slot = f"{(nums[-1] + 1) if nums else 100:03d}"
out = subprocess.run(["curl", "-sS", "-m", "1800", "-F", f"token={KEY}", "-F", f"path=ivr2:{FOLDER}/{slot}.wav", "-F", "convertAudio=1",
                      "-F", "file=@/tmp/p.mp3;filename=p.mp3", API + "UploadFile"], capture_output=True, text=True).stdout
print(f"::notice title=PATRIOTS upload::{vid} -> {FOLDER}/{slot} {os.path.getsize('/tmp/p.mp3') / 1048576:.1f}MB {out[:150]}")
if '"OK"' not in out:
    sys.exit(1)
if not TEST:
    ym("UploadTextFile", what=DONE, contents=json.dumps((done + [vid])[-60:]))
