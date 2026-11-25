"""בדיקה: ניקוד אוטומטי (נקדן של דיקטה) לטקסט שמגיע ב-TEXT. מדפיס את התשובה הגולמית מכמה כתובות."""
import json, os, urllib.request
TEXT = os.environ.get("TEXT") or "מלצר: ומה בשבילך? אורח: שכוי."
TRIES = [
    ("https://nakdan-2-0.loadbalancer.dicta.org.il/api",
     {"task": "nakdan", "data": TEXT, "genre": "modern", "addmorph": True, "keepnikud": True, "keepqq": False,
      "nodageshdefmem": False, "patachma": False, "keepmetagim": True}),
    ("https://nakdan-5-3.loadbalancer.dicta.org.il/addnikud", {"data": TEXT, "genre": "modern"}),
    ("https://nakdan-u1-0.loadbalancer.dicta.org.il/addnikud", {"data": TEXT, "genre": "modern"}),
]
for url, body in TRIES:
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "text/plain;charset=UTF-8",
                                     "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", "replace")
        print("CAT", url, "OK", len(raw), raw[:1500])
    except Exception as e:  # noqa: BLE001
        print("CAT", url, "ERR", str(e)[:200])
