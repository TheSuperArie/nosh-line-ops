"""בדיקה: קריאת קבצים ותיקיות בקו (קריאה בלבד). הפקודות מגיעות ב-INSPECT (מופרדות ב-;)."""
import json, os, re, urllib.parse, urllib.request
API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ["YM_TOKEN"].strip()
def call(name, **p):
    p["token"] = KEY
    with urllib.request.urlopen(API + name + "?" + urllib.parse.urlencode(p), timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
for c in os.environ.get("INSPECT", "").split(";"):
    c = c.strip()
    if not c:
        continue
    if c.startswith("mv "):  # mv a|b (נתיבים מלאים ivr2:)
        a, b = c[3:].split("|", 1)
        print("CAT mv", a, b, json.dumps(call("FileAction", action="move", what=a, target=b), ensure_ascii=False)[:150])
    elif c.startswith("mvall "):  # mvall /src|/dst - מעביר את כל קבצי NNN.wav/tts (לא ext.ini/M1000)
        a, b = c[6:].split("|", 1)
        d = call("GetIVR2Dir", path="ivr2:" + a)
        n = 0
        for f in (d.get("files") or []) + (d.get("ini") or []):
            nm = f.get("name") or ""
            if re.match(r"^\d{3}\.(wav|tts)$", nm):
                r = call("FileAction", action="move", what=f"ivr2:{a}/{nm}", target=f"ivr2:{b}/{nm}")
                n += r.get("responseStatus") == "OK"
        print("CAT mvall", a, b, "moved", n)
    elif c.startswith("keys "):  # keys path - שמות בכל רשימה בתשובת GetIVR2Dir
        d = call("GetIVR2Dir", path="ivr2:" + c[5:])
        print("CAT keys", c[5:], {k: [x.get("name") for x in v][:15] for k, v in d.items() if isinstance(v, list)})
    elif c.startswith("names "):  # names path - כל שמות הקבצים + תאריך
        d = call("GetIVR2Dir", path="ivr2:" + c[6:])
        print("CAT names", c[6:], [(f.get("name"), f.get("mtime")) for f in d.get("files") or []])
    elif c.startswith("raw "):  # raw path - שדות גולמיים ומספר קבצים
        d = call("GetIVR2Dir", path="ivr2:" + c[4:])
        fs = d.get("files") or []
        print("CAT raw", c[4:], "files", len(fs), "dirs", len(d.get("dirs") or []), "keys", list(d.keys()), json.dumps(fs[:2], ensure_ascii=False)[:600],
              "totalMB", round(sum(int(f.get("size") or 0) for f in fs) / 1048576, 1))
    elif c.startswith("dir "):
        d = call("GetIVR2Dir", path=c[4:])
        print("DIR", c[4:], d.get("responseStatus"), d.get("message", ""), [x.get("name") for x in d.get("dirs") or []],
              [(x.get("name"), x.get("size")) for x in d.get("files") or []][:20], [x.get("name") for x in d.get("ini") or []])
    elif c.startswith("rm "):
        r = call("FileAction", action="delete", what=c[3:])
        print("CAT rm", c[3:], json.dumps(r, ensure_ascii=False)[:200])
    elif c.startswith("put "):
        path, _, text = c[4:].partition("|")
        r = urllib.request.urlopen(urllib.request.Request(API + "UploadTextFile", data=urllib.parse.urlencode({"token": KEY, "what": path, "contents": text}).encode()), timeout=60)
        print("CAT put", path, r.read().decode()[:150])
    elif c.startswith("addnum "):  # addnum path|0500000000 - מוסיף מספר לרשימה לבנה בלי להדפיס את הרשימה
        path, _, num = c[7:].partition("|")
        num = re.sub(r"\D", "", num)
        cur = [x.strip() for x in (call("GetTextFile", what=path).get("contents") or "").splitlines() if x.strip()]
        if num in cur:
            print("CAT addnum", path, "already there ..." + num[-4:], "total", len(cur))
        else:
            cur.append(num)
            r = urllib.request.urlopen(urllib.request.Request(API + "UploadTextFile", data=urllib.parse.urlencode({"token": KEY, "what": path, "contents": "\n".join(cur)}).encode()), timeout=60)
            chk = [x.strip() for x in (call("GetTextFile", what=path).get("contents") or "").splitlines() if x.strip()]
            print("CAT addnum", path, json.loads(r.read().decode()).get("responseStatus"), "added ..." + num[-4:], "present", num in chk, "total", len(chk))
    elif c.startswith("mkext "):
        path, _, typ = c[6:].partition("|")
        print("CAT mkext", path, json.dumps(call("UpdateExtension", path=path, type=typ or "playfile"), ensure_ascii=False)[:200])
    elif c.startswith("dlh "):
        req = urllib.request.Request(API + "DownloadFile?" + urllib.parse.urlencode({"path": c[4:]}), headers={"authorization": KEY})
        with urllib.request.urlopen(req, timeout=60) as r:
            print("CAT dlh", c[4:], repr(r.read()[:60]))
    elif c.startswith("cmp "):
        name, _, local = c[4:].partition("|")
        with urllib.request.urlopen(API + "DownloadFile?" + urllib.parse.urlencode({"token": KEY, "path": name}), timeout=60) as r:
            got = r.read()
        exp = open(local, "rb").read()
        if got.startswith(b"B64:"):
            import base64
            got = base64.b64decode(got[4:])
        print("CAT cmp", name, "same" if got == exp else f"DIFF len {len(got)} vs {len(exp)}; backslashes {got.count(92)} vs {exp.count(92)}")
    elif c.startswith("dl "):
        with urllib.request.urlopen(API + "DownloadFile?" + urllib.parse.urlencode({"token": KEY, "path": c[3:]}), timeout=60) as r:
            print("CAT dl", c[3:], r.headers.get("content-type"), repr(r.read()[:200]))
    elif c.startswith("slice "):  # slice path|start|len  - קטע מקובץ טקסט
        path, a, n = (c[6:].split("|") + ["0", "3000"])[:3]
        t = call("GetTextFile", what=path).get("contents") or ""
        print("CAT slice", path, len(t), repr(t[int(a):int(a) + int(n)]))
    elif c.startswith("cat "):
        t = call("GetTextFile", what=c[4:])
        print("CAT", c[4:], t.get("responseStatus"), repr((t.get("contents") or t.get("message") or ""))[:400])
