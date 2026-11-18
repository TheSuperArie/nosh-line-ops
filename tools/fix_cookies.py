"""מתקן קובץ עוגיות שהודבק לסוד: כותרת חסרה, BOM, רווחים במקום טאבים, שורות שהתחברו.
כותב cookies.txt תקין (פורמט Netscape) ומדפיס אבחון בלי לחשוף ערכים."""
import os
import re

raw = os.environ.get("YT_COOKIES", "")
raw = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
print(f"::notice title=COOKIES_RAW::len={len(raw)} lines={raw.count(chr(10))} tabs={raw.count(chr(9))} "
      f"first={'#' if raw.lstrip().startswith('#') else '[' if raw.lstrip().startswith('[') else '{' if raw.lstrip().startswith('{') else 'other'} "
      f"youtube={raw.count('youtube.com')} netscape_hdr={'Netscape' in raw[:200]}", flush=True)
# ייצוא בפורמט JSON (אפשרות בתוסף) - ממירים לפורמט Netscape
if raw.lstrip()[:1] in "[{" and raw.strip():
    import json
    try:
        data = json.loads(raw)
        data = data if isinstance(data, list) else data.get("cookies", [])
        raw = "\n".join("\t".join([c.get("domain", ""), "TRUE" if c.get("domain", "").startswith(".") else "FALSE",
                                   c.get("path", "/"), "TRUE" if c.get("secure") else "FALSE",
                                   str(int(c.get("expirationDate") or c.get("expires") or 0)),
                                   c.get("name", ""), c.get("value", "")]) for c in data)
    except ValueError:
        pass
# אם כל הקובץ הודבק בשורה אחת - מפצלים לפני כל רשומה של דומיין
if raw.count("\n") < 3:
    raw = re.sub(r"\s+(?=\.?(?:youtube|google)\.com\s+(?:TRUE|FALSE))", "\n", raw)
rows = []
for line in raw.split("\n"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    parts = line.split("\t")
    if len(parts) < 7:
        parts = re.split(r"\s+", line, maxsplit=6)  # טאבים שהפכו לרווחים
    if len(parts) == 6:  # ערך ריק
        parts.append("")
    if len(parts) >= 7 and re.fullmatch(r"(TRUE|FALSE)", parts[1]) and re.fullmatch(r"-?\d+", parts[4]):
        rows.append("\t".join(parts[:7]))
names = {r.split("\t")[5] for r in rows}
login = "yes" if {"LOGIN_INFO", "SID"} & names or "__Secure-3PSID" in names else "no"
print(f"::notice title=COOKIES::rows={len(rows)} logged_in={login}", flush=True)
if rows:
    with open("cookies.txt", "w") as f:
        f.write("# Netscape HTTP Cookie File\n" + "\n".join(rows) + "\n")
else:
    open("cookies.txt", "w").close()
