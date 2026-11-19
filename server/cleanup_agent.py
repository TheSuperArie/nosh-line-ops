"""ניקוי בקו (כל 3 שעות): תוכניות (שלוחה 2 ותוכניות בשלוחה 8) - נמחקות אחרי 7 ימים; מבזקים - קבצי ~ ישנים;
וריקון סל המחזור של ימות (Trash), כי כל מחיקה רגילה רק מעבירה לשם ותופסת מקום."""
import json, os, re, time, urllib.parse, urllib.request
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
IL = ZoneInfo("Asia/Jerusalem")
DAY = 86400
RULES = [  # (תיקייה, כמה ימים, רק קבצי ~) - 18/11: רק תוכניות (שלוחה 2 ותוכניות בשלוחה 8); הקלטות 3/6/7 לא נמחקות
    ("/2/1/1", 7, False), ("/2/1/2", 7, False), ("/2/1/3", 7, False), ("/2/2", 7, False), ("/2/3", 7, False),
    ("/8/2/2", 7, False), ("/8/9/2", 30, False),  # 8/9/2 פודקאסט - חודש; 8/2/1 (רוזנבלום) ו-8/3 (העלון) - נשמרים 2 אחרונים ע"י הסוכן עצמו
    ("/1", 1, True), ("/8/1/1", 1, True), ("/8/1/2", 1, True), ("/8/1/3", 1, True), ("/8/1/4", 1, True), ("/8/9/1", 1, True),
]


def log(*a):
    print(datetime.now(IL).strftime("%d/%m %H:%M:%S"), "[cleanup]", *a, flush=True)


def ym(name, **p):
    p["token"] = KEY
    req = urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def age(f):
    try:
        return time.time() - datetime.strptime(f.get("mtime") or "", "%d/%m/%Y %H:%M").replace(tzinfo=IL).timestamp()
    except ValueError:
        return 0


def rm(what):
    r = ym("FileAction", action="delete", what=what)
    time.sleep(0.15)
    return r.get("responseStatus") == "OK"


def old_files():
    n = 0
    for folder, days, tilde_only in RULES:
        try:
            files = ym("GetIVR2Dir", path="ivr2:" + folder).get("files") or []
        except Exception as e:  # noqa: BLE001
            log("list failed", folder, e)
            continue
        for f in files:
            name = f.get("name") or ""
            if name in ("ext.ini", "WhiteList.ini") or name.startswith("M1000") or (tilde_only and not name.startswith("~")):
                continue
            if age(f) > days * DAY and rm(f"ivr2:{folder}/{name}"):
                n += 1
    return n


def purge(path="/Trash", depth=0):
    """מחיקה מתוך סל המחזור היא סופית. תיקיות: קודם הקבצים שבהן, ואז התיקייה."""
    n = 0
    d = ym("GetIVR2Dir", path="ivr2:" + path)
    for f in d.get("files") or []:
        if rm(f"ivr2:{path}/{f.get('name')}"):
            n += 1
    for sub in d.get("dirs") or []:
        n += purge(f"{path}/{sub.get('name')}", depth + 1)
        if depth >= 1:
            rm(f"ivr2:{path}/{sub.get('name')}")
    return n


if __name__ == "__main__":
    log("cleanup agent started")
    while True:
        try:
            a = old_files()
            b = 0
            for _ in range(10):  # כל קריאה מחזירה עד 1000 קבצים
                k = purge()
                b += k
                if k < 50:
                    break
            log(f"deleted {a} old file(s), purged {b} from trash")
        except Exception as e:  # noqa: BLE001
            log("error", str(e)[:200])
        time.sleep(3 * 3600)
