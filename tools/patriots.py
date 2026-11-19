"""מוריד סרטון מיוטיוב (עוגיות + PO token) כשמע ומעלה לשלוחה בשם קבוע (המספר הבא), ושומר רק KEEP אחרונים.
ברירת מחדל: הפטריוטים -> 2/1/3. עם FOLDER/KEEP/DONE/CH/MATCH/LABEL (מ-client_payload של "ytline") - כל סדרה אחרת,
למשל דרשת הרב ברוך רוזנבלום -> 8/2/1 (2 אחרונות)."""
import json, os, re, subprocess, sys, urllib.parse, urllib.request
API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ["YM_TOKEN"].strip()
FOLDER = (os.environ.get("FOLDER") or "/2/1/3").strip()
KEEP = int(os.environ.get("KEEP") or 6)
DONE = (os.environ.get("DONE") or "ivr2:/AgentCode/patriots_done.json").strip()
CH = (os.environ.get("CH") or "UCKEImtWikw9usC1pl_9m1nQ").strip()
MATCH = [w for w in (os.environ.get("MATCH") or "הפטריוטים|המלאה").split("|") if w]
LABEL = (os.environ.get("LABEL") or "PATRIOTS").strip()
INTRO = (os.environ.get("INTRO") or "").strip()  # parasha = לפני הדרשה מקריאים "פרשת ..." (מנוקד)
PLF = (os.environ.get("PLF") or "").strip()  # מעלים רק סרטון שנמצא בפלייליסט של הערוץ ששמו מכיל את המילה הזו


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
PL = (os.environ.get("PL") or "").strip()
if "," in vid or (not vid and PL):  # כמה סרטונים (או N אחרונים מפלייליסט, LAST) - כל אחד בריצה נפרדת, מהישן לחדש
    vids = [v for v in vid.split(",") if v]
    if not vids:
        x = urllib.request.urlopen(f"https://www.youtube.com/feeds/videos.xml?playlist_id={PL}", timeout=30).read().decode()
        ents = [(re.search(r"<published>([^<]+)", e).group(1), re.search(r"<yt:videoId>([^<]+)", e).group(1))
                for e in re.findall(r"<entry>(.*?)</entry>", x, re.S)]
        vids = [v for _, v in sorted(ents)[-int(os.environ.get("LAST") or 1):]]
    print(f"::notice title={LABEL} batch::{vids}")
    bad = 0
    for v in vids:
        bad += subprocess.run([sys.executable, __file__], env={**os.environ, "VID": v, "PL": ""}).returncode != 0
    sys.exit(1 if bad else 0)
if not vid:  # בלי מזהה - הסרטון האחרון מהערוץ שמתאים למילים
    x = urllib.request.urlopen(f"https://www.youtube.com/feeds/videos.xml?channel_id={CH}", timeout=30).read().decode()
    for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
        t = re.search(r"<title>([^<]+)", e).group(1)
        if all(w in t for w in MATCH):
            vid = re.search(r"<yt:videoId>([^<]+)", e).group(1)
            break
done = done_list()
TEST = os.environ.get("TEST") == "1"  # בדיקה: מעלה לתיקיית AgentCode ולא מסמן כבוצע
if not vid or (vid in done and not TEST):
    print(f"::notice title={LABEL}::nothing to do ({vid or 'no episode'})")
    sys.exit(0)
if PLF and not TEST:  # רק דרשות מהפלייליסטים של הסדרה (לכל חומש פלייליסט), לא כל סרטון בערוץ
    pls = subprocess.run(["yt-dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s",
                          f"https://www.youtube.com/channel/{CH}/playlists"], capture_output=True, text=True).stdout
    pls = [l.split("\t")[0] for l in pls.splitlines() if "\t" in l and PLF in l.split("\t", 1)[1]]
    found = None
    for pl in pls:
        ids = subprocess.run(["yt-dlp", "--flat-playlist", "--print", "%(id)s", f"https://www.youtube.com/playlist?list={pl}"],
                             capture_output=True, text=True).stdout.split()
        if vid in ids:
            found = pl
            break
    if not pls:
        print(f"::error title={LABEL}::no playlists matching {PLF} (YouTube listing failed?)")
        sys.exit(1)
    if not found:  # לא נמצא (או שעוד לא הוכנס לפלייליסט) - לא מסמנים כבוצע, השרת ינסה שוב
        print(f"::notice title={LABEL} skip::{vid} not in any of {len(pls)} '{PLF}' playlists")
        sys.exit(0)
    print(f"::notice title={LABEL} playlist::{vid} in {found}")
ok = False
# קודם עם עוגיות, ואם יוטיוב חוסם (403) - גם בלי עוגיות
for cookies, client in [(True, None), (True, "web_safari"), (True, "mweb"), (True, "tv"),
                        (False, None), (False, "tv"), (False, "web_safari"), (False, "mweb"), (False, "tv_simply")]:
    extra = ["--extractor-args", f"youtube:player_client={client}"] if client else []
    if cookies:
        extra += ["--cookies", "/tmp/cookies.txt"]
    for f in ("/tmp/raw", "/tmp/meta.txt"):
        if os.path.exists(f):
            os.remove(f)
    r = subprocess.run(["yt-dlp"] + extra + ["-f", "bestaudio/best", "-o", "/tmp/raw",
                        "--print-to-file", "%(title)s\t%(timestamp)s", "/tmp/meta.txt",
                        "--no-part", "--no-warnings", f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists("/tmp/raw"):
        ok = True
        print(f"::notice title={LABEL} client::{client or 'default'} cookies={cookies}")
        break
    print(f"::warning title={LABEL} {client or 'default'} cookies={cookies}::{(r.stderr or r.stdout).strip()[-300:]}")
if not ok:
    sys.exit(1)
intro = ""
if INTRO == "parasha":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import parasha
    title, _, ts = (open("/tmp/meta.txt", encoding="utf-8").read().strip().splitlines() or ["\t"])[0].partition("\t")
    intro = parasha.name(title, ts if ts.isdigit() else None)
    print(f"::notice title={LABEL} intro::{title} -> {intro or '(not found)'}")
if intro:  # קריין הקו (רמי) אומר את שם הפרשה, הפסקה קצרה, ואז הדרשה
    subprocess.run(["edge-tts", "--voice", "fr-FR-RemyMultilingualNeural", "--rate=+0%", "--text", intro + ".",
                    "--write-media", "/tmp/intro.mp3"], check=True, timeout=120)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "/tmp/intro.mp3", "-f", "lavfi", "-t", "0.7", "-i", "anullsrc=r=16000:cl=mono",
                    "-i", "/tmp/raw", "-filter_complex",
                    "[0:a]aresample=16000,aformat=channel_layouts=mono[a];[1:a]aformat=channel_layouts=mono[b];"
                    "[2:a]aresample=16000,aformat=channel_layouts=mono[c];[a][b][c]concat=n=3:v=0:a=1[o]",
                    "-map", "[o]", "-ac", "1", "-ar", "16000", "-b:a", "24k", "/tmp/p.mp3"], check=True)
else:
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
print(f"::notice title={LABEL} upload::{vid} -> {FOLDER}/{slot} {os.path.getsize('/tmp/p.mp3') / 1048576:.1f}MB {out[:150]}")
if '"OK"' not in out:
    sys.exit(1)
if not TEST:
    ym("UploadTextFile", what=DONE, contents=json.dumps((done + [vid])[-60:]))
