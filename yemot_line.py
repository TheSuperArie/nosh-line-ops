"""העלאת תוכניות לקו נווה שלום (ימות המשיח). משמש את agent.py במצב LINE_JOB.
LINE_JOB = "מקור|שם|תיקיות|תווית"   למשל  "yt14|הפטריוטים|/2/1/3,/2/2,/2/3/4|הפטריוטים"
כל תיקייה: הקובץ החדש מקבל את המספר הבא (הגבוה ביותר), הקיימים לא זזים; נשמרים עד KEEP קבצים."""
import json, os, re, subprocess, urllib.parse, urllib.request

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip()
STATE = "ivr2:/AgentCode/line_state.json"
KEEP = {"/2/2": 15, "/2/3": 15}  # ברירת מחדל 6


def job():
    j = os.environ.get("LINE_JOB", "")
    if not j:
        return None
    st, name, targets, label = (j.split("|") + ["", "", "", ""])[:4]
    return {"st": st, "name": name, "targets": [t.strip() for t in targets.split(",") if t.strip()], "label": label or name}


def call(name, **p):
    p["token"] = KEY
    req = urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load_state():
    try:
        q = urllib.parse.urlencode({"token": KEY, "path": STATE})
        with urllib.request.urlopen(API + "DownloadFile?" + q, timeout=60) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return {}


def save_state(st):
    call("UploadTextFile", what=STATE, contents=json.dumps(st, ensure_ascii=False))


def next_slot(folder, keep):
    """שמות קבועים: החדש מקבל מספר גבוה מכל הקיימים (השלוחה מנגנת מהגבוה לנמוך), והקיימים לא זזים -
    כך מי שבאמצע האזנה לא קופץ לתוכנית אחרת. מוחקים את הישנים (המספרים הנמוכים) מעבר ל-keep."""
    d = call("GetIVR2Dir", path="ivr2:" + folder)
    nums = sorted(int(m.group(1)) for f in d.get("files") or [] if (m := re.match(r"^(\d{3})\.wav$", f.get("name", ""))))
    for n in nums[:max(0, len(nums) - (keep - 1))]:
        call("FileAction", action="delete", what=f"ivr2:{folder}/{n:03d}.wav")
    nxt = (nums[-1] + 1) if nums else 100
    if nxt > 999:
        raise RuntimeError(f"{folder}: no free number")
    return f"{nxt:03d}"


def upload(path):
    """נקרא במקום העלאה לדרייב: ממיר לשמע טלפוני קטן ומעלה לכל התיקיות."""
    j = job()
    base = os.path.basename(path)
    key = f"{j['label']}|{re.sub(r'[^0-9א-תA-Za-z.]+', ' ', base).strip()[:120]}"
    st = load_state()
    if key in st:
        print(f"::notice title=LINE already::{key}", flush=True)
        return
    small = "/tmp/line_upload.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "24k", small], check=True)
    mb = os.path.getsize(small) / 1024 / 1024
    for i, folder in enumerate(j["targets"]):
        slot = next_slot(folder, KEEP.get(folder, 6))
        out = subprocess.run(["curl", "-sS", "-m", "900", "-F", f"token={KEY}", "-F", f"path=ivr2:{folder}/{slot}.wav",
                              "-F", "convertAudio=1", "-F", f"file=@{small};filename={slot}.mp3", API + "UploadFile"],
                             capture_output=True, text=True).stdout
        ok = '"OK"' in out or '"success":true' in out.replace(" ", "")
        print(f"::notice title=LINE upload::{'OK' if ok else 'FAIL'} {folder} {mb:.1f}MB {j['label']} | {out[:200]}", flush=True)
        if not ok:
            raise RuntimeError(f"Yemot upload failed {folder}: {out[:300]}")
    st[key] = {"targets": j["targets"]}
    save_state(dict(list(st.items())[-300:]))
