"""מוסיף מספר לרשימה הלבנה של הקו (ivr2:/WhiteList.ini). המספר מגיע ב-client_payload.wl (קובץ האירוע) ולא מודפס ללוג."""
import json, os, re, urllib.parse, urllib.request
KEY = os.environ["YM_TOKEN"].strip()
API = "https://www.call2all.co.il/ym/api/"
def call(name, **p):
    p["token"] = KEY
    return json.loads(urllib.request.urlopen(urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode()), timeout=60).read().decode())
ev = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
nums = [re.sub(r"\D", "", n) for n in str((ev.get("client_payload") or {}).get("wl", "")).split(",")]
nums = ["0" + n[3:] if n.startswith("972") else n for n in nums if n]
cur = (call("GetTextFile", what="ivr2:/WhiteList.ini").get("contents") or "").split("\n")
cur = [c.strip() for c in cur if c.strip()]
added = [n for n in nums if re.fullmatch(r"0\d{8,9}", n) and n not in cur]
if added:
    r = call("UploadTextFile", what="ivr2:/WhiteList.ini", contents="\n".join(cur + added))
    print("::notice title=WL::added", len(added), "total", len(cur) + len(added), r.get("responseStatus"))
else:
    print("::notice title=WL::nothing to add (already listed or invalid); total", len(cur))
