# עותק ציבורי של סוכן ההורדות - משמש רק למצב קו (LINE_JOB). המקור הפרטי: yt-agent
#!/usr/bin/env python3
"""סוכן הורדות: חיפוש ביוטיוב -> מייל עם 10 תוצאות -> הורדה לפי בחירה.

ACTION=search   : QUERY = "שם.שיר" (שיר → MP3 במייל) או "שם" / "שם.וידאו" (MP4 לדרייב)
ACTION=download : VID = מזהה הסרטון, KIND = song/video, SIG = חתימה מהקישור
"""
import glob
import hashlib
import hmac
import html
import json
import os
import re
import smtplib
import subprocess
import sys
import time
import urllib.parse
from email.message import EmailMessage
from urllib.parse import urlencode

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")
CLICK_URL = os.environ.get("MAKE_CLICK_URL", "")
SECRET = os.environ.get("LINK_SECRET", "")
DRIVE_FOLDER = os.environ.get("DRIVE_FOLDER", "הורדות/הורדות יוטיוב")
MAIL_TO = ""  # למי עונים (נקבע לפי השולח / המשתמש בקישור)
USER_KEY = ""  # "" = אריה (ברירת מחדל); מפתח קצר למשתמשים נוספים
# משתמשים נוספים: כתובת מייל -> מפתח, ולכל מפתח: לאן עונים ולאיזו תיקייה בדרייב (תיקייה משותפת איתו)
USER_BY_MAIL = json.loads(os.environ.get("USER_BY_MAIL_JSON") or "{}")  # (בעותק הציבורי: ריק - רק מצב קו)
RADIO_SHOWS = ["ינון מגל", "פתחי וזמרי", "אראל סגל", "שניים עד ארבע", "הפטריוטים", "@drucker"]  # הקיצורים של ארי ודודי
SHOW_FOLDERS = {  # תוכניות טלוויזיה עם תיקייה קבועה משלהן (משותפת עם מי שמורשה לקיצור)
    "הפטריוטים": "הורדות/תוכניות טלוויזיה/הפטריוטים",
    "@drucker": "הורדות/תוכניות טלוויזיה/רביב דרוקר",
}


def share_show_folder(key: str) -> list:
    """יוצר (אם צריך) את תיקיית התוכנית ומשתף אותה עם המשתמשים המוגבלים שמורשים לה."""
    folder, out = SHOW_FOLDERS[key], []
    run(["rclone", "mkdir", f"gdrive:{folder}"])
    for u in USERS.values():
        if key in (u.get("only") or []):
            out.append(f"{u['mail']}: {folder.rsplit('/', 1)[-1]} {'✓' if drive_share(folder, u['mail']) else '✗'}")
    return out
USERS = json.loads(os.environ.get("USERS_JSON") or "{}")  # (בעותק הציבורי: ריק - רק מצב קו)
# ג'ימייל מגביל ל-25MB כולל קידוד base64 (~33%) → קובץ עד 18MB נשלח כקובץ מצורף
MAX_ATTACH = 18 * 1024 * 1024
OUT = "out"

SONG_WORDS = {"שיר", "mp3", "אודיו", "audio"}
VIDEO_WORDS = {"וידאו", "סרטון", "mp4", "video"}


# ---------- עזרים ----------
def sign(vid: str, kind: str) -> str:
    msg = f"{vid}|{kind}" + (f"|{USER_KEY}" if USER_KEY else "")  # קישור של משתמש אחר לא יעבוד אצל אחר
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()[:20]


def ytdlp() -> list:
    args = ["yt-dlp", "--no-warnings", "--no-progress"]
    if os.path.exists("cookies.txt") and os.path.getsize("cookies.txt") > 0:
        args += ["--cookies", "cookies.txt"]
    return args


# אם יוטיוב חוסם את הלקוח הרגיל, מנסים "לקוחות" אחרים של יוטיוב לפי הסדר
# web_safari מחזיר את כל האיכויות (עד 1080) כשמשתמשים בעוגיות; השאר רק 360
# "nc" = בלי עוגיות + PO Token: נותן את כל האיכויות (עד 4K) כשיוטיוב לא דורש התחברות.
# החשבון עם העוגיות נמצא אצל יוטיוב בניסוי SABR שחוסם את רוב הלקוחות ל-360, לכן הוא רק גיבוי.
# "@il" = אותו ניסיון דרך המנהרה לשרת בישראל (כתובת ישראלית): עוקף חסימה מחוץ לישראל
# ולפעמים גם את "Sign in to confirm you're not a bot" של שרתי GitHub. מדלגים עליו אם אין מנהרה.
# 11/2026: בפועל רק הלקוח הרגיל (עם העוגיות) מצליח - nc ו-web_safari נכשלים תמיד ומבזבזים דקות, ולכן הוא ראשון
CLIENTS = [None, "nc", "web_safari", "mweb", "None@il", "nc@il", "web_safari@il"]
IL = os.environ.get("IL_PROXY", "")


FALLBACK_NOTES: list = []  # למה לקוח נכשל (לאבחון איכות נמוכה)

# לשירים: הלקוח הרגיל נותן קובץ קטן ומהיר (360p/אודיו) - הרבה יותר מהיר מ-web_safari
SONG_CLIENTS = [None, "web_safari", "web", "mweb", "nc@il", "None@il"]


def run_yt(cmd: list, clients: list | None = None) -> str:
    """מריץ yt-dlp ומנסה לקוחות שונים עד שאחד מצליח."""
    last = None
    order = clients or CLIENTS
    for entry in order:
        client, il = (entry or "None"), False
        if client.endswith("@il"):
            client, il = client[:-3], True
            if not IL:
                continue
        client = None if client == "None" else client
        label = (client or "default") + ("@il" if il else "")
        base = cmd
        if client == "nc":
            base = [a for i, a in enumerate(cmd)
                    if not (a == "--cookies" or (i > 0 and cmd[i - 1] == "--cookies"))]
        extra = ["--extractor-args", f"youtube:player_client={client}"] if client and client != "nc" else []
        if il:
            extra += ["--proxy", IL]
        try:
            out = run(base[:1] + extra + base[1:])
            if entry != order[0]:
                print(f"::notice title=CLIENT used::{label}", flush=True)
            return out
        except RuntimeError as err:
            last = err
            reason = " | ".join(l.strip() for l in str(err).splitlines() if "ERROR" in l or "WARNING" in l)[-400:]
            print(f"::warning title=CLIENT {label} failed::{reason or str(err)[-300:]}", flush=True)
            FALLBACK_NOTES.append(f"{label}: {reason or str(err)[-200:]}")
    if last is None:
        raise RuntimeError("no client available")
    raise last


def run(cmd: list) -> str:
    if os.environ.get("LINE_JOB") and cmd[:1] == ["rclone"] and len(cmd) > 2 and cmd[1] in ("copy", "move") \
            and os.path.isfile(cmd[2]):  # מצב קו טלפוני: במקום דרייב - לימות המשיח
        import yemot_line
        yemot_line.upload(cmd[2])
        return ""
    if os.environ.get("LINE_JOB") and cmd[:1] == ["rclone"]:  # מצב קו: בלי פעולות דרייב (אין חיבור לדרייב)
        print("::notice title=LINE skip::" + " ".join(cmd)[:150], flush=True)
        return ""
    print("$", " ".join(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout[-3000:], p.stderr[-3000:], sep="\n", flush=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout)[-1500:])
    return p.stdout


def send_mail(subject: str, body_html: str, attachment: str | None = None) -> None:
    if os.environ.get("LINE_JOB"):  # מצב קו טלפוני: בלי מיילים
        print(f"::notice title=LINE mail skipped::{subject[:120]}", flush=True)
        return
    msg = EmailMessage()
    msg["From"] = f"סוכן הורדות <{GMAIL_USER}>"
    msg["To"] = MAIL_TO or GMAIL_USER
    msg["Subject"] = subject
    msg.set_content("המייל הזה מוצג כ-HTML.")
    msg.add_alternative(
        f'<div dir="rtl" style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6">{body_html}{quota_line()}</div>',
        subtype="html",
    )
    if attachment:
        with open(attachment, "rb") as f:
            msg.add_attachment(f.read(), maintype="audio", subtype="mpeg",
                               filename=os.path.basename(attachment))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.send_message(msg)
    print("mail sent:", subject, flush=True)


def quota_line() -> str:
    """הערכה כמה הורדות נשארו החודש ב-Make (בלי מפתח של Make):
    סופרים את ההרצות מאז האיפוס האחרון (14 בחודש). חיפוש ≈ 2 פעולות, לחיצה ≈ 3, מתוך 1,000."""
    import datetime, urllib.request
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        reset = now.replace(day=14, hour=8, minute=25, second=0, microsecond=0)
        if now < reset:
            reset = (reset.replace(day=1) - datetime.timedelta(days=1)).replace(day=14)
        nxt = (reset.replace(day=28) + datetime.timedelta(days=7)).replace(day=14)
        counts = {"search": 0, "download": 0}
        page = 1
        while True:
            url = (f"https://api.github.com/repos/{os.environ['REPO']}/actions/runs?event=repository_dispatch"
                   f"&created=>={reset:%Y-%m-%dT%H:%M:%SZ}&per_page=100&page={page}")
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                                       "Accept": "application/vnd.github+json"})
            runs = json.load(urllib.request.urlopen(req, timeout=15)).get("workflow_runs", [])
            for r in runs:
                t = r.get("display_title", "")
                if t in counts:
                    counts[t] += 1
                else:  # הרצות ישנות בלי שם - מניחים חצי-חצי
                    counts["search" if r["id"] % 2 else "download"] += 1
            if len(runs) < 100:
                break
            page += 1
        used = counts["search"] * 2 + counts["download"] * 3
        left = max(0, (1000 - used) // 5)
        return (f"<p style='color:#999;font-size:12px;border-top:1px solid #eee;padding-top:8px'>"
                f"נשארו החודש בערך <b>{left}</b> הורדות (מתאפס ב-{nxt:%d/%m}).</p>")
    except Exception as err:  # noqa: BLE001
        print("quota failed:", err, flush=True)
        return ""


def add_upload_dates(entries: list) -> None:
    """החיפוש המהיר לא מחזיר תאריך העלאה - שולפים אותו בקריאה אחת לכל התוצאות."""
    if not entries:
        return
    try:
        p = subprocess.run(ytdlp() + (["--proxy", IL] if IL else []) + ["--skip-download", "--ignore-errors", "--no-playlist",
                                      "--print", "%(id)s %(upload_date)s"]
                           + [f"https://www.youtube.com/watch?v={e['id']}" for e in entries],
                           capture_output=True, text=True, timeout=240)
        dates = dict(l.split(" ", 1) for l in p.stdout.splitlines() if " " in l)
        for e in entries:
            d = dates.get(e["id"], "")
            if re.fullmatch(r"\d{8}", d):
                e["upload_date"] = d
    except Exception as err:  # noqa: BLE001
        print("upload dates failed:", err, flush=True)


def fmt_date(d) -> str:
    return f"עלה {d[6:8]}/{d[4:6]}/{d[:4]}" if d and len(str(d)) == 8 else ""


def fmt_dur(sec) -> str:
    if not sec:
        return ""
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_views(n) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M צפיות"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K צפיות"
    return f"{n} צפיות"


NEW_WORDS = {"חדש", "חדשים", "new"}
PL_URL = re.compile(r"[?&]list=([A-Za-z0-9_-]{10,64})")
CH_URL = re.compile(r"youtube\.com/(@[^/?#\s]+|channel/UC[A-Za-z0-9_-]{22}|c/[^/?#\s]+|user/[^/?#\s]+)")
PL_MAX = 1000  # הכי הרבה פריטים מפלייליסט אחד
PL_CHUNK = 40  # כמה פריטים בכל חלק (כל חלק רץ במחשב נפרד)
YT_URL = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")


def parse_query(raw: str) -> tuple[str, str, bool]:
    """'לאן הולכים כולם.שיר.חדש' -> ('לאן הולכים כולם', 'song', True). ברירת מחדל: וידאו, לפי רלוונטיות."""
    raw = re.sub(r"^\s*((fwd?|re|הועבר)\s*:\s*)+", "", raw.strip(), flags=re.I).strip()
    kind, newest = "video", False
    while True:
        m = re.match(r"^(.*?)\s*\.\s*([^.\s]+)\s*$", raw)
        word = m.group(2).lower() if m else ""
        if word in SONG_WORDS:
            kind = "song"
        elif word in VIDEO_WORDS:
            kind = "video"
        elif word in NEW_WORDS:
            newest = True
        else:
            break
        raw = m.group(1).strip()
    return raw, kind, newest


def click_button(item_id: str, k: str, text: str, strong: bool = False) -> str:
    params = {'id': item_id, 'k': k, 's': sign(item_id, k)}
    if USER_KEY:
        params['u'] = USER_KEY
    link = f"{CLICK_URL}?{urlencode(params)}"
    style = ("display:inline-block;margin:6px 0 0 6px;padding:5px 12px;border-radius:14px;"
             "text-decoration:none;font-size:13px;font-weight:bold;"
             + ("background:#1a56b0;color:#fff;" if strong else "background:#eef2f8;color:#1a56b0;"))
    return f'<a href="{html.escape(link)}" style="{style}">{text}</a>'


def playlist_offer(list_id: str, kind: str) -> None:
    """קישור לפלייליסט: מייל עם פרטי הפלייליסט וכפתורים להורדת הכול לתיקייה בדרייב."""
    url = f"https://www.youtube.com/playlist?list={list_id}"
    try:
        info = json.loads(run(ytdlp() + ["--flat-playlist", "-J", "--playlist-items", f"1-{PL_MAX}", url]))
    except RuntimeError as err:
        send_mail("סוכן הורדות: אי אפשר לפתוח את הפלייליסט",
                  "<p>יוטיוב לא אפשר לגשת לפלייליסט הזה (אולי הוא פרטי, נמחק או חסום מחוץ לישראל).</p>"
                  f"<pre dir='ltr' style='font-size:11px;white-space:pre-wrap;color:#888'>{html.escape(str(err)[-400:])}</pre>")
        return
    entries = [e for e in info.get("entries") or [] if e and e.get("id")]
    title = info.get("title") or list_id
    total = sum(int(e.get("duration") or 0) for e in entries)
    more = info.get("playlist_count") or len(entries)
    choices = [("plsong", "🎵 הכול כשירים"), ("plv480", "480"), ("plv720", "720"), ("plv1080", "1080")]
    if kind != "song":
        choices = choices[1:] + choices[:1]
    preferred = "plsong" if kind == "song" else "plv480"
    buttons = "".join(click_button(list_id, k, t, k == preferred) for k, t in choices)
    listing = "".join(
        f"<li><bdi>{html.escape(e.get('title') or e['id'])}</bdi>"
        f"<span style='color:#999'> · {fmt_dur(e.get('duration'))}</span></li>" for e in entries[:15])
    if len(entries) > 15:
        listing += f"<li style='color:#999'>ועוד {len(entries) - 15}...</li>"
    limit_note = (f"<p style='color:#a15c00;font-size:13px'>בפלייליסט יש {more} פריטים - יירדו רק {PL_MAX} הראשונים.</p>"
                  if more and int(more) > PL_MAX else "")
    body = (
        f"<p>פלייליסט: <b><bdi>{html.escape(title)}</bdi></b></p>"
        f"<p style='color:#777;font-size:13px'>{len(entries)} פריטים · בערך {fmt_dur(total) or '?'} בסך הכול</p>"
        f"{limit_note}"
        f"<p>לחץ כדי להוריד את <b>כל הפלייליסט</b> לתיקייה משלו בתוך <b>{html.escape(DRIVE_FOLDER)}</b> בדרייב:</p>"
        f"<p>{buttons}</p>"
        f"<p style='color:#777;font-size:13px'>שירים = MP3, מספרים = וידאו באיכות הזו. פלייליסט ארוך לוקח זמן - "
        f"תקבל מייל כשהכול עלה.</p>"
        f"<ol style='font-size:13px;color:#444'>{listing}</ol>"
    )
    send_mail(f"פלייליסט: {title}", body)


def channel_offer(path: str, kind: str) -> None:
    """קישור לערוץ: מוצאים את פלייליסט ההעלאות שלו ומציעים להוריד: רק סרטונים רגילים, או הכול (כולל שורטס)."""
    from urllib.parse import unquote
    url = f"https://www.youtube.com/{path}"
    try:
        info = json.loads(run(ytdlp() + ["--flat-playlist", "-J", "--playlist-items", "1", url]))
    except RuntimeError as err:
        send_mail("סוכן הורדות: אי אפשר לפתוח את הערוץ",
                  f"<pre dir='ltr' style='font-size:11px;white-space:pre-wrap;color:#888'>{html.escape(str(err)[-400:])}</pre>")
        return
    cid = info.get("channel_id") or ""
    name = info.get("channel") or info.get("uploader") or unquote(path)
    if not cid.startswith("UC"):
        send_mail("סוכן הורדות: לא מצאתי את הערוץ", f"<p>לא הצלחתי לזהות את הערוץ <bdi>{html.escape(unquote(path))}</bdi>.</p>")
        return
    sections = []
    for prefix, label in (("UULF", "סרטונים רגילים (בלי שורטס)"), ("UU", "הכול - כולל שורטס ושידורים")):
        lid = prefix + cid[2:]
        try:
            pi = json.loads(run(ytdlp() + ["--flat-playlist", "-J", "--playlist-items", f"1-{PL_MAX}",
                                           f"https://www.youtube.com/playlist?list={lid}"]))
        except RuntimeError:
            continue
        ents = [e for e in pi.get("entries") or [] if e and e.get("id")]
        if not ents:
            continue
        dur = sum(int(e.get("duration") or 0) for e in ents)
        choices = [("plsong", "🎵 הכול כשירים"), ("plv480", "480"), ("plv720", "720"), ("plv1080", "1080")]
        if kind != "song":
            choices = choices[1:] + choices[:1]
        preferred = "plsong" if kind == "song" else "plv480"
        buttons = "".join(click_button(lid, k, t, k == preferred) for k, t in choices)
        sections.append(
            f"<div style='border:1px solid #e5e5e5;border-radius:8px;padding:10px 12px;margin:10px 0'>"
            f"<b>{label}</b><br><span style='color:#777;font-size:13px'>{len(ents)} פריטים"
            f"{' · בערך ' + fmt_dur(dur) if dur else ''}</span><br>{buttons}</div>")
    if not sections:
        send_mail("סוכן הורדות: לא נמצאו סרטונים בערוץ", f"<p>בערוץ <bdi>{html.escape(name)}</bdi> לא נמצאו סרטונים.</p>")
        return
    send_mail(f"ערוץ: {name}",
              f"<p>ערוץ: <b><bdi>{html.escape(name)}</bdi></b></p>"
              f"<p style='color:#777;font-size:13px'>בחר מה להוריד. הכול עולה לתיקייה משלו בתוך "
              f"<b>{html.escape(DRIVE_FOLDER)}</b> בדרייב, ותקבל מייל סיכום בסוף. לחיצה חוזרת ממשיכה מאיפה שעצר "
              f"ומורידה רק סרטונים חדשים.</p>{''.join(sections)}")


# ---------- חיפוש ----------
# ---------- חיפוש תוכניות רדיו: "שם התוכנית" בנושא + "103" / "גלי" בגוף ההודעה ----------
STATION_WORDS = {"103": r"(?<![\d.])103(?:fm|FM)?(?!\d)|רדיו 103", "gly": r"גלי ישראל|גלי(?![\w])",
                 "12": r"(?<![\d.:/])12(?![\d.:/])|ערוץ 12|קשת 12|מאקו",
                 "13": r"(?<![\d.:/])13(?![\d.:/])|ערוץ 13|רשת 13"}


def station_marker() -> tuple[str, str]:
    """(תחנה, שאילתה) - לפי מילה בגוף ההודעה (לפני הציטוט/החתימה) או סיומת בנושא: '.103' / '.גלי'."""
    q = os.environ.get("QUERY", "").strip()
    m = re.search(r"\s*\.(103|12|13|גלי(?: ישראל)?)\s*$", q)
    if m:
        return (m.group(1) if m.group(1) in ("103", "12", "13") else "gly"), q[:m.start()].strip()
    body = []
    for line in os.environ.get("BODY", "").splitlines():
        if line.strip().startswith((">", "--")) or re.match(r"\s*(On |ב.{0,40}כתב|בתאריך)", line):
            break
        body.append(line)
    text = " ".join(body)[:300]
    for st, rx in STATION_WORDS.items():
        if re.search(rx, text):
            return st, q
    return "", q


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (t or "").replace("־", " "))).strip().lower()


def _best(query: str, names: list) -> int:
    words = [w for w in _norm(query).split() if len(w) > 1]
    best, best_s = -1, 0
    for i, n in enumerate(names):
        nn = _norm(n)
        sc = sum(1 for w in words if w in nn) + (0.5 if _norm(query) in nn else 0)
        if sc > best_s:
            best, best_s = i, sc
    return best


OMNY_FEEDS = {  # תוכניות 103 שהפיד שלהן ב-Omny (כפתור RSS בדף התוכנית)
    "אראל סגל": "https://www.omnycontent.com/d/playlist/178d72a7-a889-4132-8008-a5cc014ed109/70b9dbd1-1145-4daa-ad6d-b1ab00ab51df/d7f009d3-e14c-46d1-a4f8-b1ab00abb1c6/podcast.rss",
}


def station_items(st: str, query: str) -> tuple[str, list, list]:
    """(שם התוכנית, פרקים [{title,date,mp3,full}], רשימת כל התוכניות) - או ('', [], names) אם לא נמצאה."""
    import html as _h
    if st == "103":
        home = fetch_text("https://103fm.maariv.co.il/")
        slugs = []
        for m in re.finditer(r'href="(?:https://103fm\.maariv\.co\.il)?/program/([^"#?]+?)\.aspx"', home):
            if m.group(1) not in slugs:
                slugs.append(m.group(1))
        names = [urllib.parse.unquote(sl).replace("-", " ") for sl in slugs]
        i = _best(query, names)
        if i < 0:
            return "", [], names
        page = fetch_text(f"https://103fm.maariv.co.il/program/{slugs[i]}.aspx")
        pid = re.search(r"programId=(\d+)", page)
        omny = re.search(r"https://www\.omnycontent\.com/d/playlist/[^\"'\s<>]+?/podcast\.rss", page)
        omny = omny.group(0) if omny else next((u for k, u in OMNY_FEEDS.items() if _norm(k) in _norm(names[i])), "")
        rss = (fetch_text(f"https://103fm.maariv.co.il/rss/mediarss.aspx?programId={pid.group(1)}") if pid else
               fetch_text(omny) if omny else "")
        show = _h.unescape((re.search(r"<channel>.*?<title>(.*?)</title>", rss, re.S) or [None, names[i]])[1]).strip()
        from email.utils import parsedate_to_datetime
        items = []
        for it in re.findall(r"<item>(.*?)</item>", rss, re.S):
            g = lambda tag: _h.unescape((re.search(rf"<{tag}>(.*?)</{tag}>", it, re.S) or [None, ""])[1]).strip()
            enc = re.search(r'<enclosure url="([^"]+)"', it)
            if not enc:
                continue
            try:
                d = parsedate_to_datetime(g("pubDate"))
            except Exception:  # noqa: BLE001
                d = None
            items.append({"title": g("title"), "date": d.strftime("%d/%m/%Y") if d else "", "ts": d.timestamp() if d else 0,
                          "mp3": _h.unescape(enc.group(1)),
                          "full": "המלאה" in g("title") or "התוכנית המלאה" in g("itunes:summary") or (bool(omny) and "<itunes:episodeType>full" in it)})
        return show, items, names
    # גלי ישראל: רשימת התוכניות מה-API, הפרקים מתוך דף התוכנית (__NEXT_DATA__)
    progs = []
    for ep in ("GetProgramShowOnTopMenu", "GetPodcastShowOnTopMenu"):
        try:
            progs += json.loads(fetch_text(f"https://api_new.radiodarom.co.il/api/Program/{ep}?radioStationId=2") or "[]")
        except ValueError:
            pass
    names = [re.sub(r"\s*\|\s*\d{1,2}:\d{2}.*$", "", p.get("title") or "") for p in progs]
    i = _best(query, [n + " " + (p.get("subTitle") or "") for n, p in zip(names, progs)])
    if i < 0:
        return "", [], names
    page = fetch_text(f"https://www.gly.co.il/program?id={progs[i]['id']}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    data = json.loads(m.group(1)) if m else {}
    pd = ((data.get("props") or {}).get("pageProps") or {}).get("programData") or {}
    items = []
    for day in pd.get("itemsByDate") or []:
        for it in day.get("items") or []:
            if not it.get("item_stream_url"):
                continue
            dd = it.get("itemDate") or day.get("date") or ""
            full = bool(it.get("isFullProgram"))
            title = (it.get("itemTitle") or "").strip()
            if full and len(title) < 12:
                title = f"התוכנית המלאה {dd}"
            try:
                ts = time.mktime(time.strptime(re.sub(r"\D", ".", dd), "%d.%m.%Y"))
            except (ValueError, OverflowError):
                ts = 0
            items.append({"title": title, "date": dd.replace(".", "/"), "ts": ts, "mp3": it["item_stream_url"], "full": full})
    return names[i].strip(), items, names


MAKO_PAGES = {  # דפי תוכניות במאקו שלא מופיעים ברשימת התוכניות של קשת
    "המהדורה המרכזית": "https://www.mako.co.il/mako-vod-channel2-news/main-newscast",
}


def do_tv_search(query: str, pick: bool = False):
    """ערוץ 12 (מאקו): מחפש את התוכנית לפי השם ומחזיר את הפרקים האחרונים עם כפתורי הורדה."""
    import site_dl
    shows = {}
    direct = [k for k in MAKO_PAGES if _norm(k) in _norm(query) or _norm(query) in _norm(k)]
    if direct:  # תוכניות חדשות (מהדורה מרכזית וכו') - לא נמצאות ברשימת התוכניות של קשת
        show_url, show = MAKO_PAGES[direct[0]], direct[0]
        return _tv_episodes(show, show_url, pick)
    links, texts = [], {}
    for page in ("https://www.mako.co.il/mako-vod-channel2-news", "https://www.mako.co.il/mako-vod-keshet"):  # חדשות 12 + קשת
        try:
            site_dl.find_streams(page, links_out=links, play=False, texts_out=texts)
        except Exception as e:
            print(f"::warning title=TV list::{page} {str(e)[:200]}", flush=True)
    for h, t in texts.items():
        m = re.match(r"https://www\.mako\.co\.il/(mako-vod-(?:keshet|channel2-news)/[^/?#]+)/?$", h)
        if m and t and not site_dl.EPISODE_RE.search(h):
            t = re.sub(r"\s*[-|]\s*(צילום|יח\"?צ|באדיבות|קרדיט).*$", "", t).strip() or t
            shows.setdefault(h, t)
    urls, names = list(shows), list(shows.values())
    i = _best(query, [f"{n} {u.rsplit('/', 1)[-1].replace('_', ' ')}" for u, n in zip(urls, names)])
    print(f"::notice title=TV::q={query[:40]} | {len(links)} links, {len(shows)} shows -> {names[i][:50] if i >= 0 else '-'}", flush=True)
    if i < 0 and pick:
        return None, None
    if i < 0:
        lst = "".join(f"<li>{html.escape(n)}</li>" for n in sorted(set(names))[:80])
        send_mail(f"📺 לא מצאתי תוכנית בשם '{query[:40]}' בקשת 12",
                  f"<p>כתוב בנושא את שם התוכנית, ובגוף ההודעה 12.</p><p>תוכניות שמצאתי:</p><ul>{lst}</ul>")
        return
    return _tv_episodes(names[i], urls[i], pick)


def _tv_episodes(show: str, show_url: str, pick: bool = False):
    import site_dl
    l2, t2 = [], {}
    site_dl.find_streams(show_url, links_out=l2, play=False, texts_out=t2)
    eps = site_dl.episode_links(show_url, l2)[0]
    eps = [e for e in eps if site_dl.EPISODE_RE.search(e.split("?")[0])] or eps  # רק פרקים, לא עונות
    def _d(e):  # תאריך בתחילת שם הפרק (09.11.26) - החדש ראשון
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", t2.get(e) or "")
        return (int(m.group(3)) % 100, int(m.group(2)), int(m.group(1))) if m else (0, 0, 0)
    if any(_d(e) != (0, 0, 0) for e in eps):
        eps = sorted(eps, key=_d, reverse=True)
    eps = eps[:10]
    print(f"::notice title=TV show::{show[:50]} | {len(eps)} episodes | {show_url}", flush=True)
    if pick:
        return show, (eps[0] if eps else None)
    if not eps:
        send_mail(f"📺 {show}: לא מצאתי פרקים", f"<p>לא מצאתי פרקים בדף התוכנית.</p><p dir='ltr'>{html.escape(show_url)}</p>")
        return
    rows = ""
    for e in eps:
        t = t2.get(e) or next((v for k, v in t2.items() if k.split("?")[0].rstrip("/") == e.split("?")[0].rstrip("/") and v), "") or e.rsplit("/", 1)[-1]
        rows += (f"<div style='border-bottom:1px solid #e3e3e3;padding:8px 0'><b>{html.escape(t[:120])}</b><br>"
                 f"{click_button(e, 'tv', '⬇️ הורד (איכות מלאה)', True)}{click_button(e, 'tv480', '480 (קובץ קטן)')}</div>")
    send_mail(f"📺 {show} - הפרקים האחרונים",
              f"<p>הפרקים האחרונים של <b>{html.escape(show)}</b>:</p>{rows}"
              f"<p style='color:#777;font-size:12px'>הפרק יעלה לדרייב, לתיקייה {html.escape(SITE_FOLDER)}.</p>")


def kaltura13(path: str, body: dict) -> dict:
    import urllib.request
    req = urllib.request.Request("https://5031.frp1.ott.kaltura.com/api_v3/service/" + path,
                                 json.dumps({"apiVersion": "5.4.0", **body}).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=40).read().decode()).get("result") or {}


def drucker_show() -> str:
    """השם הנוכחי של התוכנית היומית של רביב דרוקר (משתנה: אזור בחירה וכו') - לפי לוח השידורים של 13."""
    try:
        ks = kaltura13("ottuser/action/anonymousLogin", {"partnerId": 5031})["ks"]
        objs = kaltura13("asset/action/list", {"ks": ks, "filter": {"objectType": "KalturaSearchAssetFilter",
                         "kSql": "(and description~'רביב דרוקר' asset_type='epg')", "orderBy": "START_DATE_DESC"},
                         "pager": {"objectType": "KalturaFilterPager", "pageSize": 50, "pageIndex": 1}}).get("objects") or []
        now = time.time()
        objs = [o for o in objs if (o.get("startDate") or 0) <= now and "יומית" in (o.get("description") or "")
                and not re.search(r"המקור|הקברניטים", o.get("name") or "")]
        if objs:
            name = max(objs, key=lambda o: o.get("startDate") or 0)["name"].strip()
            print(f"::notice title=DRUCKER::{name} ({len(objs)} epg)", flush=True)
            return name
    except Exception as e:
        print(f"::warning title=DRUCKER::{str(e)[:300]}", flush=True)
    return "אזור בחירה"


def do_13_search(query: str, pick: bool = False):
    """רשת 13: חיפוש התוכנית במערכת הווידאו שלהם (Kaltura) והפרקים האחרונים עם כפתורי הורדה."""
    ks = kaltura13("ottuser/action/anonymousLogin", {"partnerId": 5031})["ks"]
    q = re.sub(r"['\"\\\\()]", " ", query).strip()
    def lst(ksql, size=50):
        return kaltura13("asset/action/list", {"ks": ks, "filter": {"objectType": "KalturaSearchAssetFilter", "kSql": ksql,
                         "typeIn": "1268", "orderBy": "CREATE_DATE_DESC"},
                         "pager": {"objectType": "KalturaFilterPager", "pageSize": size, "pageIndex": 1}}).get("objects") or []
    meta = lambda o, k: str(((o.get("metas") or {}).get(k) or {}).get("value") or "")
    sname = lambda o: (((o.get("tags") or {}).get("SeriesName") or {}).get("objects") or [{}])[0].get("value") or ""
    found = lst(f"(or SeriesName~'{q}' name~'{q}')")
    series = {}
    for o in found:
        if meta(o, "SeriesID"):
            series.setdefault(meta(o, "SeriesID"), sname(o) or o.get("name", ""))
    ids, names = list(series), list(series.values())
    i = _best(query, names)
    print(f"::notice title=TV13::q={query[:40]} | {len(found)} hits, {len(series)} series -> {names[i] if i >= 0 else '-'}", flush=True)
    if i < 0 and pick:
        return None, None
    if i < 0:
        send_mail(f"📺 לא מצאתי תוכנית בשם '{query[:40]}' ברשת 13",
                  "<p>כתוב בנושא את שם התוכנית, ובגוף ההודעה 13.</p>")
        return
    sid, show = ids[i], names[i]
    eps = sorted(lst(f"(and SeriesID='{sid}')", 50), key=lambda o: max(o.get("createDate") or 0, o.get("startDate") or 0), reverse=True)[:10]
    if pick:
        return show, (f"https://13tv.co.il/allshows/series/{sid}/season/{meta(eps[0], 'SeasonNumber') or 1}/{eps[0]['id']}" if eps else None)
    rows = ""
    for o in eps:
        url = f"https://13tv.co.il/allshows/series/{sid}/season/{meta(o, 'SeasonNumber') or 1}/{o['id']}"
        when = time.strftime("%d/%m/%Y", time.localtime(max(o.get("createDate") or 0, o.get("startDate") or 0)))
        rows += (f"<div style='border-bottom:1px solid #e3e3e3;padding:8px 0'><b>{html.escape(o.get('name', ''))}</b>"
                 f"<div style='color:#777;font-size:12px'>{when}</div>"
                 f"{click_button(url, 'tv', '⬇️ הורד (איכות מלאה)', True)}{click_button(url, 'tv480', '480 (קובץ קטן)')}</div>")
    send_mail(f"📺 {show} - הפרקים האחרונים",
              (f"<p>הפרקים האחרונים של <b>{html.escape(show)}</b> ברשת 13:</p>{rows}" if rows else
               f"<p>מצאתי את <b>{html.escape(show)}</b> אבל בלי פרקים לצפייה.</p>")
              + f"<p style='color:#777;font-size:12px'>הפרק יעלה לדרייב, לתיקייה {html.escape(SITE_FOLDER)}.</p>")


def do_station_search(st: str, query: str) -> None:
    if st == "13":
        do_13_search(query)
        return
    if st == "12":
        do_tv_search(query)
        return
    label = {"103": "רדיו 103FM", "gly": "רדיו גלי ישראל"}[st]
    show, items, names = station_items(st, query)
    print(f"::notice title=STATION::{st} q={query[:40]} -> {show[:50]} | {len(items)} items | {len(names)} programs", flush=True)
    if not show:
        lst = "".join(f"<li>{html.escape(n)}</li>" for n in names[:60])
        send_mail(f"📻 לא מצאתי תוכנית בשם '{query[:40]}' ב{label}",
                  f"<p>כתוב בנושא את שם התוכנית (או שם המגיש), ובגוף ההודעה {html.escape('103' if st == '103' else 'גלי')}.</p>"
                  f"<p>התוכניות שמצאתי ב{label}:</p><ul>{lst}</ul>")
        return
    items.sort(key=lambda i: i["ts"], reverse=True)
    full = [i for i in items if i["full"]][:6]
    parts = [i for i in items if not i["full"]][:8]
    def row(it):
        vid = f"{it['mp3']}|{show}|{it['title']}"[:700] + ("|f" if it["full"] else "|s")
        badge = ("<span style='background:#1a56b0;color:#fff;border-radius:8px;padding:1px 7px;font-size:11px'>תוכנית מלאה</span> "
                 if it["full"] else "")
        return (f"<div style='border-bottom:1px solid #e3e3e3;padding:8px 0'>{badge}<b>{html.escape(it['title'])}</b>"
                f"<div style='color:#777;font-size:12px'>{it['date']}</div>{click_button(vid, 'radio', '⬇️ הורד MP3', it['full'])}</div>")
    body = (f"<p>הפרקים האחרונים של <b>{html.escape(show)}</b> ב{label}:</p>"
            + ("<h3 style='margin:12px 0 4px'>תוכניות מלאות</h3>" + "".join(row(i) for i in full) if full else "")
            + ("<h3 style='margin:14px 0 4px'>קטעים וראיונות</h3>" + "".join(row(i) for i in parts) if parts else "")
            + "<p style='color:#777;font-size:12px'>קובץ קטן מגיע במייל, תוכנית מלאה עולה לדרייב (תיקייה בשם התוכנית).</p>")
    if not items:
        body = f"<p>מצאתי את <b>{html.escape(show)}</b>, אבל אין בה עכשיו פרקים להורדה.</p>"
    send_mail(f"📻 {show} - הפרקים האחרונים", body)


def radio_folder(show: str, full: bool) -> str:
    """הורדות רדיו / תוכניות מלאות|קטעים / <תוכנית>"""
    return f"{RADIO_FOLDER}/{'תוכניות מלאות' if full else 'קטעים'}/{safe_name(show)}"


def drive_id(remote: str) -> str:
    """מזהה תיקייה/קובץ בדרייב (lsjson --stat לא תמיד עובד על תיקיות - מחפשים ברשימה של התיקייה שמעל)."""
    parent, _, name = remote.strip("/").rpartition("/")
    r = subprocess.run(["rclone", "lsjson", f"gdrive:{parent}"], capture_output=True, text=True)
    for e in json.loads(r.stdout or "[]"):
        if e.get("Name") == name:
            return e.get("ID", "")
    print(f"::warning title=DRIVE id::{remote} not found {(r.stderr or '')[-200:]}", flush=True)
    return ""


def drive_share(remote: str, email: str, folder_id: str = "") -> bool:
    """משתף תיקייה שהסוכן יצר עם מייל (צפייה בלבד). בלי מייל התראה מגוגל."""
    import urllib.request
    try:
        folder_id = folder_id or drive_id(remote)  # (גם מרענן את ה-token של rclone)
        if folder_id and not remote:
            subprocess.run(["rclone", "lsd", "gdrive:"], capture_output=True, text=True)
        if not folder_id:
            return False
        conf = open(os.path.expanduser("~/.config/rclone/rclone.conf"), encoding="utf-8").read()
        tok = json.loads(re.search(r"^token = (.*)$", conf, re.M).group(1))["access_token"]
        req = urllib.request.Request(f"https://www.googleapis.com/drive/v3/files/{folder_id}/permissions?sendNotificationEmail=false",
                                     json.dumps({"role": "reader", "type": "user", "emailAddress": email}).encode(),
                                     {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        for attempt in range(3):  # מיד אחרי יצירת תיקייה גוגל מחזיר לפעמים 403 - מנסים שוב
            try:
                urllib.request.urlopen(req, timeout=60).read()
                break
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                time.sleep(8)
        print(f"::notice title=SHARE::{remote or folder_id} -> {email}", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        body = e.read().decode()[:300] if hasattr(e, "read") else ""
        print(f"::warning title=SHARE::{remote or folder_id} -> {email}: {str(e)[:200]} {body}", flush=True)
        return False


def drive_unshare(folder_id: str, email: str) -> None:
    import urllib.request
    conf = open(os.path.expanduser("~/.config/rclone/rclone.conf"), encoding="utf-8").read()
    tok = json.loads(re.search(r"^token = (.*)$", conf, re.M).group(1))["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    perms = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files/{folder_id}/permissions?fields=permissions(id,emailAddress)", headers=h), timeout=60).read())
    for pm in perms.get("permissions", []):
        if (pm.get("emailAddress") or "").lower() == email.lower():
            urllib.request.urlopen(urllib.request.Request(f"https://www.googleapis.com/drive/v3/files/{folder_id}/permissions/{pm['id']}",
                                                          headers=h, method="DELETE"), timeout=60)
            print(f"::notice title=UNSHARE::{folder_id} -> {email}", flush=True)


def do_radio_item(vid: str) -> None:
    flag = ""
    if vid[-2:] in ("|f", "|s"):  # f = תוכנית מלאה, s = קטע
        vid, flag = vid[:-2], vid[-1]
    mp3, show, title = (vid.split("|", 2) + ["", ""])[:3]
    full = flag == "f" or (not flag and "מלאה" in title)
    if not re.match(r"https://[\w.-]+\.(streamgates\.net|maariv\.co\.il|co\.il)/", mp3):
        sys.exit("bad radio url")
    os.makedirs(OUT, exist_ok=True)
    name = safe_name(f"{show} - {title}")[:110] + ".mp3"
    path = os.path.join(OUT, name)
    for px in [""] + ([IL.replace("socks5://", "")] if IL else []):
        r = subprocess.run(["curl", "-sSL", "-m", "1800", "--retry", "2", "-o", path]
                           + (["--socks5-hostname", px] if px else []) + [mp3], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 20_000:
            break
    else:
        send_mail(f"⚠️ לא הורד: {title[:60]}", f"<p>לא הצלחתי להוריד את הקובץ.</p><p dir='ltr' style='color:#777;font-size:12px'>{html.escape(mp3)}</p>")
        return
    mb = os.path.getsize(path) / 1024 / 1024
    print(f"::notice title=RADIO item::{mb:.1f}MB {name[:80]}", flush=True)
    folder = radio_folder(show, full)
    run(["rclone", "copy", path, f"gdrive:{folder}"])
    if USERS.get(USER_KEY, {}).get("only"):  # משתמש מוגבל: רואה רק את תיקיות התוכניות שלו
        drive_share(folder, MAIL_TO)
    link = drive_link_html(f"{folder}/{name}")
    if os.path.getsize(path) <= MAX_ATTACH:
        send_mail(f"🎧 {title[:70]}", f"<p><b>{html.escape(show)}</b> - {html.escape(title)} ({mb:.1f}MB) מצורף "
                  f"(ונשמר גם בתיקייה <b>{html.escape(folder)}</b>).</p>{link}", path)
        return
    send_mail(f"📻 עלה לדרייב: {title[:60]}",
              f"<p><b>{html.escape(name)}</b> ({mb:.0f}MB) עלה לתיקייה <b>{html.escape(folder)}</b> בדרייב.</p>{link}")


# ---------- קיצורים: נושא קצר = התוכנית המלאה האחרונה (בלי חיפוש) ----------
SHORTCUTS = [  # (מילות קיצור בנושא, מקור, שם לחיפוש, תיאור)  מקור: 103 / gly / 12 / 13 / yt14 (ערוץ 14 ביוטיוב)
    (["ינון ובן", "ינון ובן כספית", "ינון מגל", "מגל וכספית"], "103", "ינון מגל", "ינון מגל ובן כספית (103)"),
    (["הראל סגל", "אראל סגל", "אראל סגל 103"], "103", "אראל סגל", "אראל סג\"ל (103)"),
    (["שנים עד ארבע", "שניים עד ארבע", "שתיים עד ארבע", "רון ויואב"], "103", "שניים עד ארבע", "שניים עד ארבע - רון שלום ויואב כהן (103)"),
    (["פתחי וזמרי", "פתחי וזמרי בעם", "פתחי"], "gly", "פתחי וזמרי", "פתחי וזמרי בעם (גלי ישראל)"),
    (["שרון גל", "שרון גל גלי"], "gly", "שרון גל", "שרון גל (גלי ישראל)"),
    (["אבי רצון", "אבי רצון בבוקר"], "gly", "אבי רצון בבוקר", "אבי רצון בבוקר (גלי ישראל)"),
    (["דיבורים כמו חול", "דיבורים"], "gly", "דיבורים כמו חול", "דיבורים כמו חול (גלי ישראל)"),
    (["הפטריוטים", "פטריוטים"], "yt14", "הפטריוטים", "הפטריוטים (ערוץ 14)"),
    (["פתחי ושי"], "yt14", "פתחי ושי", "פתחי ושי (ערוץ 14)"),
    (["מהדורת 14", "14", "מהדורה 14"], "yt14", "המהדורה המרכזית", "המהדורה המרכזית (ערוץ 14)"),
    (["12", "מהדורת 12", "מהדורה 12"], "12", "המהדורה המרכזית", "המהדורה המרכזית (קשת 12)"),
    (["13", "מהדורת 13", "מהדורה 13"], "13", "המהדורה המרכזית", "המהדורה המרכזית (רשת 13)"),
    (["רביב דרוקר", "דרוקר", "אזור בחירה"], "13", "@drucker", "התוכנית היומית של רביב דרוקר (רשת 13, בשבע)"),
    (["המקור"], "13", "המקור", "המקור - תחקירים (רשת 13)"),
    (["פגוש את העיתונות"], "12", "פגוש את העיתונות", "פגוש את העיתונות (חדשות 12)"),
]


def shortcut_for(q: str):
    """(מקור, שם, תיאור, mp3?) אם הנושא הוא קיצור. 'mp3' בסוף = רק שמע."""
    n = _norm(q)
    audio = bool(re.search(r"\s*(mp3|אמפי ?3|שמע)$", n, re.I))
    n = re.sub(r"\s*(mp3|אמפי ?3|שמע)$", "", n, flags=re.I).strip()
    for words, st, name, label in SHORTCUTS:
        if n in [_norm(w) for w in words]:
            return st, name, label, audio
    return None


def yt14_latest(name: str, chan: str = r"C14|עכשיו 14|ערוץ 14", tag: str = "C14"):
    """הסרטון המלא האחרון של תוכנית מערוץ 14 ביוטיוב (C14) - או מערוץ אחר (chan)."""
    out = ""
    for q in (f"{name} התוכנית המלאה", f"{name} התוכנית המלאה {tag}"):  # יוטיוב לא תמיד ממיין לפי תאריך - אוספים משני חיפושים
        for extra in ([], ["--proxy", IL] if IL else None):
            if extra is None:
                continue
            r = subprocess.run(["yt-dlp", "--flat-playlist", "--no-warnings", "--print", "%(id)s\t%(channel)s\t%(duration)s\t%(title)s",
                                "--playlist-end", "40", "https://www.youtube.com/results?sp=CAI%3D&search_query="
                                + urllib.parse.quote(q)] + extra, capture_output=True, text=True, timeout=180)
            if r.stdout.strip():
                out += r.stdout
                break
            print("::warning title=YT14 search::" + (r.stderr or "empty")[-400:], flush=True)
    import urllib.request as _ureq
    if tag == "C14":  # ה-RSS של הערוץ מתעדכן מיד; החיפוש ביוטיוב מפגר בשעות
        try:
            with _ureq.urlopen("https://www.youtube.com/feeds/videos.xml?channel_id=UCKEImtWikw9usC1pl_9m1nQ", timeout=30) as r:
                x = r.read().decode("utf-8", "replace")
            for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
                vid = re.search(r"<yt:videoId>([^<]+)", e)
                t = re.search(r"<title>([^<]+)", e)
                if vid and t and "המלאה" in t.group(1):
                    out += f"{vid.group(1)}\tC14\t3600\t{html.unescape(t.group(1))}\n"
        except Exception as ex:  # noqa: BLE001
            print(f"::warning title=YT14 rss::{ex}", flush=True)
    words = [w for w in _norm(name).split() if len(w) > 1]
    cands = []
    for line in out.splitlines():
        vid, ch, dur, title = (line.split("\t") + ["", "", "", ""])[:4]
        try:
            dur = float(dur)
        except ValueError:
            dur = 0
        nt = _norm(title)
        dm = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", title)  # תאריך בכותרת - לא לקחת תוכנית ישנה
        if dm:
            y = int(dm.group(3)) + (2000 if len(dm.group(3)) == 2 else 0)
            try:
                if time.time() - time.mktime((y, int(dm.group(2)), int(dm.group(1)), 0, 0, 0, 0, 0, -1)) > 45 * 86400:
                    continue
            except (OverflowError, ValueError):
                pass
        if re.search(chan, ch) and all(w in nt for w in words) and ("המלאה" in title or dur >= 1800) \
                and "שידור החי" not in title and dur >= 600:
            ts = 0
            if dm:
                try:
                    ts = time.mktime((int(dm.group(3)) + (2000 if len(dm.group(3)) == 2 else 0), int(dm.group(2)), int(dm.group(1)), 0, 0, 0, 0, 0, -1))
                except (OverflowError, ValueError):
                    pass
            if vid not in [c[1] for c in cands]:
                cands.append((ts, vid, title))
    if cands and not any(c[0] for c in cands):  # בלי תאריך בכותרת - בודקים את תאריך ההעלאה
        for c in list(cands):
            u = subprocess.run(["yt-dlp", "--no-warnings", "--skip-download", "--print", "%(upload_date)s", c[1]],
                               capture_output=True, text=True, timeout=120).stdout.strip()
            try:
                ts = time.mktime(time.strptime(u, "%Y%m%d"))
            except ValueError:
                continue
            cands.remove(c)
            if time.time() - ts <= 45 * 86400:
                cands.append((ts, c[1], c[2]))
    if cands:
        cands.sort(key=lambda c: c[0], reverse=True)  # החדש ביותר לפי התאריך שבכותרת
        return cands[0][1], cands[0][2]
    print("::notice title=YT14 nomatch::" + (" ## ".join(out.splitlines()[:12]) or "(no results)")[:3000], flush=True)
    return None, None


LEDGER = "הורדות/.agent/latest.json"  # מה כבר ירד דרך הקיצורים (כדי לא להוריד פעמיים)


def folder_files(folder: str) -> list:
    r = subprocess.run(["rclone", "lsjson", "--files-only", f"gdrive:{folder}"], capture_output=True, text=True)
    try:
        return [f["Name"] for f in json.loads(r.stdout or "[]")]
    except ValueError:
        return []


def ledger_read() -> dict:
    try:
        return json.loads(subprocess.run(["rclone", "cat", f"gdrive:{LEDGER}"], capture_output=True, text=True).stdout or "{}")
    except ValueError:
        return {}


def ledger_write(key: str, entry: dict) -> None:
    led = ledger_read()
    led[key] = entry
    subprocess.run(["rclone", "rcat", f"gdrive:{LEDGER}"], input=json.dumps(led, ensure_ascii=False), text=True, capture_output=True)


def latest_once(key: str, folder: str, what: str, runner, share=None) -> None:
    """מריץ הורדה של קיצור רק אם הקובץ לא ירד כבר; אחרת מייל 'כבר ירד' עם קישורים."""
    try:
        led = json.loads(subprocess.run(["rclone", "cat", f"gdrive:{LEDGER}"], capture_output=True, text=True).stdout or "{}")
    except ValueError:
        led = {}
    e = led.get(key)
    if e and e.get("name") in folder_files(e.get("folder", "")):
        print(f"::notice title=ALREADY::{key[:80]} -> {e['folder']}/{e['name'][:60]}", flush=True)
        if share:
            share()
        send_mail(f"✅ כבר ירד: {what[:70]}",
                  f"<p>הקובץ <b>{html.escape(e['name'])}</b> כבר הורד לתיקייה <b>{html.escape(e['folder'])}</b>, אז לא הורדתי אותו שוב.</p>"
                  f"{drive_link_html(e['folder'] + '/' + e['name'])}{drive_link_html(e['folder'], True)}")
        return
    before = set(folder_files(folder))
    runner()
    new = [n for n in folder_files(folder) if n not in before]
    if new:
        led[key] = {"folder": folder, "name": new[0], "at": time.strftime("%Y-%m-%d %H:%M")}
        subprocess.run(["rclone", "rcat", f"gdrive:{LEDGER}"], input=json.dumps(led, ensure_ascii=False), text=True, capture_output=True)
    if share:
        share()


def do_latest(st: str, name: str, audio: bool = False) -> None:
    """התוכנית המלאה האחרונה - ישר להורדה (רדיו = MP3; טלוויזיה = וידאו, או MP3 אם ביקשו)."""
    if st in ("103", "gly"):
        show, items, _ = station_items(st, name)
        full = sorted([i for i in items if i["full"]], key=lambda i: i["ts"], reverse=True)
        print(f"::notice title=LATEST::{st} {name} -> {show} | {len(full)} full", flush=True)
        if not full:
            send_mail(f"⚠️ לא נמצאה תוכנית מלאה: {name}", "<p>לא מצאתי עכשיו תוכנית מלאה להורדה. נסה שוב מאוחר יותר.</p>")
            return
        folder = radio_folder(show, True)
        limited = bool(USERS.get(USER_KEY, {}).get("only"))
        latest_once("radio|" + full[0]["mp3"], folder, f"{show} - {full[0]['title']}",
                    lambda: do_radio_item(f"{full[0]['mp3']}|{show}|{full[0]['title']}|f"),
                    (lambda: drive_share(folder, MAIL_TO)) if limited else None)
        return
    global DRIVE_FOLDER, SITE_FOLDER
    key = name
    if key in SHOW_FOLDERS:
        DRIVE_FOLDER = SITE_FOLDER = SHOW_FOLDERS[key]
    if st in ("yt14", "yt11"):
        vid, title = yt14_latest(name) if st == "yt14" else yt14_latest(name, r"כאן|kan|Kan", "כאן חדשות")
        print(f"::notice title=LATEST::yt14 {name} -> {vid} {str(title)[:80]}", flush=True)
        if not vid:
            send_mail(f"⚠️ לא מצאתי את התוכנית המלאה האחרונה: {name}",
                      "<p>בערוץ היוטיוב של ערוץ 14 לא עלתה לאחרונה תוכנית מלאה של התוכנית הזו (יש שם רק קטעים).</p>")
            return
        kind = "song" if audio else "v720"
        os.environ.update(VID=vid, KIND=kind, SIG=sign(vid, kind))
        latest_once(f"yt|{vid}|{kind}", DRIVE_FOLDER, str(title), do_download,
                    (lambda: share_show_folder(key)) if key in SHOW_FOLDERS else None)
        return
    if name == "@drucker":
        name = drucker_show()
    show, url = (do_13_search if st == "13" else do_tv_search)(name, pick=True)
    print(f"::notice title=LATEST::{st} {name} -> {show} | {url}", flush=True)
    if not url:
        send_mail(f"⚠️ לא מצאתי את הפרק האחרון: {name}", "<p>לא מצאתי עכשיו פרק להורדה. נסה שוב מאוחר יותר.</p>")
        return
    if audio:
        os.environ["AUDIO_ONLY"] = "1"
    latest_once(f"tv|{url}|{'mp3' if audio else 'video'}", SITE_FOLDER, str(show), lambda: do_site(url),
                (lambda: share_show_folder(key)) if key in SHOW_FOLDERS else None)


def do_rss_line(feed: str) -> None:
    """פודקאסט (למשל גלצ ב-Omny): הפרק האחרון -> הקו."""
    import html as _h
    rss = fetch_text(feed)
    it = (re.findall(r"<item>(.*?)</item>", rss, re.S) or [""])[0]
    enc = re.search(r'<enclosure url="([^"]+)"', it)
    title = _h.unescape((re.search(r"<title>(.*?)</title>", it, re.S) or [None, "פרק"])[1]).strip()
    if not enc:
        print("::warning title=RSS::no item", flush=True)
        return
    path = "/tmp/" + re.sub(r"[^\w\u0590-\u05FF .-]+", " ", title).strip()[:80] + ".mp3"
    subprocess.run(["curl", "-sSL", "-m", "1800", "-o", path, _h.unescape(enc.group(1))], check=True)
    print(f"::notice title=RSS::{title} {os.path.getsize(path)/1e6:.0f}MB", flush=True)
    run(["rclone", "copy", path, "gdrive:line"])


def allowed_shortcuts() -> list:
    only = USERS.get(USER_KEY, {}).get("only")
    return [sc for sc in SHORTCUTS if not only or sc[2] in only]


def do_limited_menu() -> None:
    """תפריט למשתמש שמורשה רק לקיצורים מסוימים."""
    scs = allowed_shortcuts()
    rows = "".join(f"<div style='margin:8px 0'>{click_button(f'{st}|{name}', 'last', '⬇️ ' + label, True)}"
                   + (click_button(f'{st}|{name}|mp3', 'last', '🎧 רק שמע (MP3)') if st not in ("103", "gly") else "") + "</div>"
                   for _, st, name, label in scs)
    send_mail("📻 התוכניות שלך - הורדת התוכנית המלאה האחרונה",
              "<p><b>שמור את המייל הזה</b> (כוכב ⭐). כל לחיצה מורידה את התוכנית המלאה האחרונה:</p>" + rows +
              "<hr><p>או: שלח מייל לסוכן, ובנושא רק אחד מהשמות האלה (בלי לכתוב כלום בגוף):</p><ul>"
              + "".join(f"<li><b>{html.escape(w[0])}</b> - {html.escape(l)}</li>" for w, _, _, l in scs) + "</ul>"
              + "<p>בתוכניות טלוויזיה מגיע וידאו; כדי לקבל רק שמע - להוסיף <b>mp3</b> אחרי השם (למשל: הפטריוטים mp3).</p>"
              + "<p>כל קובץ עולה לתיקייה של התוכנית בדרייב (משותפת איתך), ובמייל שמגיע יש קישור אליו.</p>")


def do_menu() -> None:
    if USERS.get(USER_KEY, {}).get("only"):
        do_limited_menu()
        return
    rows = "".join(f"<div style='margin:8px 0'>{click_button(f'{st}|{name}', 'last', '⬇️ ' + label + ' - התוכנית המלאה האחרונה', True)}</div>"
                   for _, st, name, label in SHORTCUTS)
    send_mail("📋 התפריט של סוכן ההורדות",
              "<p><b>שמור את המייל הזה</b> (כוכב ⭐). כל לחיצה על כפתור מורידה את התוכנית המלאה האחרונה:</p>" + rows +
              "<hr><p><b>קיצורים בנושא המייל</b> (בלי לכתוב כלום בגוף):</p><ul>"
              + "".join(f"<li><b>{html.escape(w[0])}</b> - {html.escape(l)}, התוכנית המלאה האחרונה</li>" for w, _, _, l in SHORTCUTS)
              + "</ul><p><b>חיפוש רגיל:</b> שם בנושא - תוצאות מיוטיוב, ובתחתית כפתורים לחפש את אותו שם גם ב-12, 13, 103 וגלי ישראל.</p>"
              "<p>או ישירות: שם בנושא + בגוף <b>12</b> / <b>13</b> / <b>103</b> / <b>גלי</b>.</p>")


def do_search() -> None:
    _q = os.environ.get("QUERY", "").strip()
    if _norm(_q) in ("תפריט", "קיצורים", "menu"):
        do_menu()
        return
    sc = shortcut_for(_q)
    if sc and not os.environ.get("BODY", "").strip()[:1].isdigit():
        do_latest(sc[0], sc[1], sc[3])
        return
    st, st_q = station_marker()
    if st and st_q and not re.search(r"https?://", st_q):
        do_station_search(st, st_q)
        return
    raw = os.environ.get("QUERY", "").strip()
    if not raw:  # אם הנושא ריק – לוקחים את השורה הראשונה מגוף המייל
        raw = next((l.strip() for l in os.environ.get("BODY", "").splitlines() if l.strip()), "")
    # קישור לספוטיפיי (שיר / אלבום / פלייליסט) - מורידים מיוטיוב עם התגיות של ספוטיפיי
    import spotify
    sp_m = spotify.SPOTIFY_URL.search(raw) or spotify.SPOTIFY_URL.search(os.environ.get("BODY", ""))
    if sp_m:
        do_spotify(sp_m.group(0))
        return
    # קישור לפלייליסט - הצעה להוריד את כולו
    pl_m = PL_URL.search(raw) or PL_URL.search(os.environ.get("BODY", ""))
    if pl_m:
        playlist_offer(pl_m.group(1), parse_query(raw)[1])  # ".שיר" גם כשהוא צמוד לקישור
        return
    # קישור לערוץ - הצעה להוריד את כל הסרטונים שלו
    ch_m = CH_URL.search(raw) or CH_URL.search(os.environ.get("BODY", ""))
    if ch_m:
        channel_offer(ch_m.group(1), parse_query(raw)[1])
        return
    # קישור ישיר ליוטיוב (בנושא או בגוף המייל) - בלי חיפוש
    link_m = YT_URL.search(raw) or YT_URL.search(os.environ.get("BODY", ""))
    query, kind, newest = parse_query(re.sub(r"\S*youtu\S*", "", raw).strip() if link_m else raw)
    if link_m:
        kind = parse_query(raw)[1]
    if link_m:
        try:
            info = json.loads(run_yt(ytdlp() + ["-J", "--no-playlist",
                                                f"https://www.youtube.com/watch?v={link_m.group(1)}"]))
        except RuntimeError as err:
            msg = str(err)
            low = msg.lower()
            geo = any(w in low for w in ("country", "geo", "region", "video unavailable"))
            where = ("הסוכן ניסה גם דרך השרת בישראל ולא הצליח." if IL else
                     "הסוכן רץ על שרת בחו\"ל, ולכן יוטיוב לא נותן לו לגשת לסרטון.")
            send_mail("סוכן הורדות: אי אפשר להוריד את הקישור הזה",
                      (f"<p><b>כנראה שהסרטון חסום לצפייה מחוץ לישראל</b> (או שהוא נמחק/פרטי). {where}</p>"
                       if geo else "<p>יוטיוב לא אפשר לגשת לסרטון הזה.</p>")
                      + f"<pre dir='ltr' style='font-size:11px;white-space:pre-wrap;color:#888'>{html.escape(msg[-400:])}</pre>")
            return
        query = query or info.get("title") or link_m.group(1)
        entries = [info]
    if not link_m and not query:
        send_mail("סוכן הורדות: לא התקבל שם לחיפוש",
                  "<p>שלח מייל שבנושא שלו השם, לדוגמה: <b>לאן הולכים כולם.שיר</b></p>")
        return

    if not link_m:
        # דף החיפוש של יוטיוב כמו בישראל (gl=IL) - ytsearch הרגיל מחזיר לפעמים 0 תוצאות מהשרת בחו"ל
        from urllib.parse import quote
        url = f"https://www.youtube.com/results?search_query={quote(query)}&gl=IL&hl=he"
        if newest:  # .חדש = מהחדש לישן
            url += "&sp=CAI%253D"
        # חיפוש מכתובת ישראלית (דרך המנהרה) - כך מופיעים גם סרטונים שחסומים מחוץ לישראל
        search_cmd = ytdlp() + ["--flat-playlist", "-J", "--playlist-items", "1-25", url]
        out = ""
        if IL:
            try:
                out = run(search_cmd[:1] + ["--proxy", IL] + search_cmd[1:])
                print("::notice title=SEARCH::via Israel", flush=True)
            except RuntimeError as err:
                print(f"::warning title=SEARCH IL failed::{str(err)[-300:]}", flush=True)
        if not out:
            out = run(search_cmd)
        entries = [e for e in json.loads(out).get("entries", [])
                   if e and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(e.get("id") or ""))][:10]
        add_upload_dates(entries)

    if not entries:
        send_mail(f"לא נמצאו תוצאות: {query}", f"<p>לא נמצאו תוצאות עבור <b>{html.escape(query)}</b>.</p>")
        return

    # כפתורים לכל תוצאה: שיר או וידאו באיכות לבחירה. מה שביקשת בנושא מופיע ראשון ומודגש.
    choices = [("song", "🎵 שיר"), ("v480", "480"), ("v720", "720"), ("v1080", "1080")]
    if kind != "song":
        choices = choices[1:] + choices[:1]
    preferred = "song" if kind == "song" else "v720"

    def button(vid: str, k: str, text: str) -> str:
        return click_button(vid, k, text, k == preferred)

    rows = []
    for i, e in enumerate(entries, 1):
        vid = e["id"]
        # <bdi> שומר על סדר נכון כשמערבבים עברית ואנגלית (למשל @ בשם הערוץ)
        parts = [e.get("channel") or e.get("uploader") or "", fmt_dur(e.get("duration")),
                 fmt_views(e.get("view_count")), fmt_date(e.get("upload_date"))]
        meta = " · ".join(f"<bdi>{html.escape(x)}</bdi>" for x in parts if x)
        buttons = "".join(button(vid, k, t) for k, t in choices)
        rows.append(
            f'<tr><td style="padding:12px 6px;border-bottom:1px solid #e5e5e5;vertical-align:top;color:#888">{i}.</td>'
            f'<td style="padding:12px 6px;border-bottom:1px solid #e5e5e5">'
            f'<b style="color:#222"><bdi>{html.escape(e.get("title") or vid)}</bdi></b><br>'
            f'<span style="color:#777;font-size:13px">{meta}</span><br>{buttons}</td></tr>'
        )
    body = (
        f"<p>{'הסרטון מהקישור ששלחת' if link_m else 'תוצאות עבור'} <b>{html.escape(query)}</b>"
        f"{' (מהחדש לישן)' if newest and not link_m else ''}</p>"
        f"<p style='color:#777;font-size:13px'>לחץ על כפתור: <b>🎵 שיר</b> = MP3 למייל, "
        f"<b>480 / 720 / 1080</b> = וידאו לדרייב באיכות הזו (480 חוסך הכי הרבה מקום). "
        f"ייפתח דף אישור, והקובץ יגיע תוך כמה דקות.</p>"
        f'<table style="border-collapse:collapse;width:100%">{"".join(rows)}</table>'
    )
    if not link_m and query:
        body += ("<p style='margin-top:16px'><b>לחפש את זה במקום אחר:</b><br>"
                 + click_button(query[:120], "q12", "📺 קשת 12") + click_button(query[:120], "q13", "📺 רשת 13")
                 + click_button(query[:120], "q103", "📻 רדיו 103") + click_button(query[:120], "qgly", "📻 גלי ישראל") + "</p>")
    send_mail(f"תוצאות: {query}", body)


# ---------- אתרים אחרים (מאקו, 13 וכו') ----------
SITE_FOLDER = os.environ.get("SITE_FOLDER", "הורדות/הורדות אתרים")
RADIO_FOLDER = os.environ.get("RADIO_FOLDER", "הורדות/הורדות רדיו")  # 103 / גלי ישראל - תיקייה נפרדת
ANY_URL = re.compile(r"https?://[^\s<>\"'\]\)]+")
NOT_SITE = re.compile(r"(youtube\.com|youtu\.be|google\.com|gmail\.com|make\.com|github\.com|spotify\.com|spotify\.link)", re.I)
BAD_LINK = re.compile(r"(unsubscribe|opt-out|list-preferences|mailsuite|mailtrack|signature|privacy)", re.I)


def site_link() -> str:
    """קישור לאתר שאינו יוטיוב - קודם מהנושא, ואם אין אז מגוף המייל."""
    subject = os.environ.get("QUERY", "")
    texts = [subject]
    # גוף המייל נבדק רק כשהנושא ריק - אחרת הנושא הוא חיפוש רגיל
    # (בתחתית המיילים יש קישורי חתימה/ביטול מנוי שאינם סרטונים)
    if not re.sub(r"[.\s]|שיר|חדש|וידאו|480|720", "", subject):
        texts.append(os.environ.get("BODY", ""))
    for text in texts:
        for u in ANY_URL.findall(text):
            if not NOT_SITE.search(u) and not BAD_LINK.search(u):
                return re.sub(r"\.(480|720|1080)$", "", u.rstrip(".,;"))  # ".720" שהודבק צמוד לקישור
    return ""


SITE_BUDGET_MIN = int(os.environ.get("SITE_BUDGET_MIN", "105"))  # לא מתחילים פרק חדש אחרי X דקות (מגבלת הג'וב 150)


def clean_title(t: str) -> str:
    t = re.sub(r"\s*[|\-–]\s*(רשת 13|mako|12\+|N12).*$", "", html.unescape(t or "")).strip()
    t = re.sub(r'[\\/:*?"<>|]+', " ", t)
    t = re.sub(r"^\s*VOD\b", "", t)
    return re.sub(r"\s{2,}", " ", t)[:90].strip(" -–") or "סרטון"


def site_episode(url: str, max_h: int, folder: str, tag: str = "") -> dict:
    """מוריד פרק אחד מעמוד שלו ומעלה לדרייב. מחזיר פרטים, או why=live/drm/none אם אין מה להוריד."""
    import site_dl
    links: list = []
    title, found = site_dl.find_streams(url, links_out=links)
    title = clean_title(title)
    print(f"::notice title=SITE page::{title[:80]} | {len(found)} streams | {len(links)} links | {url[:200]}", flush=True)
    stream, why = site_dl.pick_best(found)
    if not stream or stream["info"]["duration"] < 600 and len(site_dl.episode_links(url, links)[0]) >= 2:
        return {"why": why or "list", "title": title, "links": links}
    os.makedirs(OUT, exist_ok=True)
    name = f"{title} [{tag}]" if tag else title
    path = os.path.join(OUT, name + ".mp4")
    site_dl.download(stream, path, max_h)
    if os.environ.get("AUDIO_ONLY"):  # רק שמע: MP3
        mp3 = path[:-4] + ".mp3"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vn", "-c:a", "libmp3lame", "-b:a", "128k", mp3], check=True)
        os.remove(path)
        path, name = mp3, name
    size_mb = os.path.getsize(path) / 1024 / 1024
    got = "MP3" if os.environ.get("AUDIO_ONLY") else subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    print(f"::notice title=SITE video::{got}p {size_mb:.0f}MB (stream max {stream['info']['height']}p) {name[:60]}", flush=True)
    global DRIVE_FOLDER
    DRIVE_FOLDER = folder
    link = drive_upload(path)
    os.remove(path)
    return {"why": "", "title": title, "name": name, "size": size_mb, "got": got, "link": link,
            "max": stream["info"]["height"], "mins": int(stream["info"]["duration"] // 60)}


SOCIAL_RE = re.compile(r"https?://(?:[\w-]+\.)?(?:facebook\.com|fb\.watch|fb\.com)/", re.I)


def do_social(url: str) -> None:
    """סרטון מפייסבוק: yt-dlp מוריד ישירות (בלי דפדפן), קודם מ-GitHub ואם נחסם - דרך ישראל. עולה ל'הורדות אתרים'."""
    raw = os.environ.get("QUERY", "") + " " + os.environ.get("BODY", "")
    max_h = 480 if re.search(r"\.480\b", raw) else 720 if re.search(r"\.720\b", raw) else 1080
    os.makedirs("out", exist_ok=True)
    base = ["yt-dlp", "--no-warnings", "--no-progress", "--no-playlist",
            "-f", f"bv*[height<={max_h}]+ba/b[height<={max_h}]/bv*+ba/b", "--merge-output-format", "mp4",
            "--replace-in-metadata", "title", r"^.*?(views|צפיות|reactions|תגובות)[^|｜]*[|｜]\s*", "",
            "-o", "out/%(title).70B [%(id)s].%(ext)s", "--print", "after_move:filepath", url]
    tries = [[]] + ([["--proxy", IL]] if IL else [])
    for extra in tries:  # שידור חי שעוד רץ: לא מקליטים (זה יכול להימשך שעות) - מבקשים לשלוח שוב בסיום
        try:
            meta = json.loads(run(base[:1] + extra + ["--no-warnings", "-J", "--no-playlist", url]))
        except (RuntimeError, ValueError):
            continue
        print(f"::notice title=FB meta::live={meta.get('is_live')} status={meta.get('live_status')} dur={meta.get('duration')} {str(meta.get('title'))[:60]}", flush=True)
        if meta.get("is_live") or meta.get("live_status") in ("is_live", "is_upcoming"):
            send_mail("⏳ השידור בפייסבוק עדיין חי",
                      "<p>השידור הזה <b>עדיין משודר בשידור חי</b>. אפשר להוריד אותו רק אחרי שהוא מסתיים "
                      "ופייסבוק שומרת אותו כסרטון (בדרך כלל כמה דקות אחרי הסיום).</p>"
                      "<p>שלח שוב את אותו קישור אחרי שהשידור נגמר.</p>"
                      f"<p dir='ltr' style='color:#777;font-size:12px'>{html.escape(url)}</p>")
            return
        break
    path, err = "", ""
    for extra in tries:
        try:
            out = run(base[:1] + extra + base[1:])
            path = [l for l in out.splitlines() if l.strip().endswith(".mp4")][-1].strip()
            break
        except (RuntimeError, IndexError) as e:
            err = str(e)
            print(f"::warning title=FB::{'IL' if extra else 'direct'} failed: {err[-300:]}", flush=True)
    if not path or not os.path.exists(path):
        private = re.search(r"log ?in|login|cookies|private|registered users|not available|Cannot parse data", err, re.I)
        why = ("כנראה שהסרטון <b>לא ציבורי</b> (קבוצה סגורה, פרופיל פרטי או סטורי) - פייסבוק מראה אותו רק למי שמחובר. "
               "אפשר להוריד רק סרטונים שרואים בלי להתחבר לפייסבוק." if private else
               "לא הצלחתי להוריד את הסרטון מהקישור הזה. כדאי לבדוק שהקישור פותח סרטון (ולא פוסט בלי וידאו), ולנסות שוב.")
        send_mail("⚠️ לא הורד מפייסבוק", f"<p>{why}</p><p dir='ltr' style='color:#777;font-size:12px'>{html.escape(url)}</p>")
        return
    name = os.path.basename(path)
    size_mb = os.path.getsize(path) / 1024 / 1024
    try:
        h = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                            "-of", "json", path])).get("streams", [{}])[0].get("height")
    except Exception:  # noqa: BLE001
        h = None
    print(f"::notice title=FB video::{h}p {size_mb:.0f}MB {name[:80]}", flush=True)
    run(["rclone", "copy", path, f"gdrive:{SITE_FOLDER}"])
    link = drive_link_html(f"{SITE_FOLDER}/{name}")
    q = f"<p>איכות: <b>{h}p</b></p>" if h else ""
    send_mail(f"📁 עלה לדרייב: {name.rsplit('.', 1)[0][:60]}",
              f"<p>הסרטון מפייסבוק <b>{html.escape(name)}</b> ({size_mb:.0f}MB) עלה לתיקייה "
              f"<b>{html.escape(SITE_FOLDER)}</b> בדרייב שלך.</p>{q}{link}")


RADIO_RE = re.compile(r"https?://(?:www\.)?103fm\.maariv\.co\.il/", re.I)


def fetch_text(url: str) -> str:
    """דף/פיד: קודם ישירות, ואם נכשל - דרך השרת בישראל."""
    for px in [""] + ([IL.replace("socks5://", "")] if IL else []):
        cmd = ["curl", "-sL", "-m", "90", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36"]
        r = subprocess.run(cmd + (["--socks5-hostname", px] if px else []) + [url], capture_output=True)
        t = r.stdout.decode("utf-8", "replace")
        if r.returncode == 0 and len(t) > 500 and "Server Error" not in t[:3000]:
            return t
    return ""


def do_radio(url: str) -> None:
    """103FM: קישור לתוכנית -> התוכנית המלאה האחרונה (או N אחרונות עם ‎.N), קישור לפרק -> אותו פרק. MP3 לדרייב."""
    import html as _h
    from email.utils import parsedate_to_datetime
    m_n = re.search(r"\.(\d{1,2})$", url.strip())  # "...aspx.5" = 5 התוכניות האחרונות
    if m_n:
        url = url.strip()[:m_n.start()]
    else:
        m_n = re.search(r"(?:^|\s)\.?(\d{1,2})\s*$", os.environ.get("QUERY", "").replace(url, " "))
    want = min(int(m_n.group(1)), 30) if m_n else 1
    page = fetch_text(url)
    pid = re.search(r"programId=(\d+)", url + " " + page)
    if not pid:
        prog = re.search(r'href="(/program/[^"]+?\.aspx)"', page)
        if prog:
            pid = re.search(r"programId=(\d+)", fetch_text("https://103fm.maariv.co.il" + prog.group(1)))
    if not pid:
        send_mail("⚠️ לא הורד מ-103FM", "<p>לא הצלחתי לזהות את התוכנית בקישור. שלח קישור לדף של התוכנית "
                  "(103fm.maariv.co.il/program/...) או לפרק מתוך 'תוכניות מלאות'.</p>"
                  f"<p dir='ltr' style='color:#777;font-size:12px'>{html.escape(url)}</p>")
        return
    rss = fetch_text(f"https://103fm.maariv.co.il/rss/mediarss.aspx?programId={pid.group(1)}")
    show = _h.unescape((re.search(r"<channel>.*?<title>(.*?)</title>", rss, re.S) or [None, "103FM"])[1]).strip()
    items = []
    for it in re.findall(r"<item>(.*?)</item>", rss, re.S):
        g = lambda tag: _h.unescape((re.search(rf"<{tag}>(.*?)</{tag}>", it, re.S) or [None, ""])[1]).strip()
        enc = re.search(r'<enclosure url="([^"]+)"', it)
        if not enc:
            continue
        try:
            when = parsedate_to_datetime(g("pubDate"))
        except Exception:  # noqa: BLE001
            when = None
        items.append({"title": g("title"), "link": g("link"), "mp3": _h.unescape(enc.group(1)), "when": when})
    one = re.search(r"ZrqvnVq=(\w+)", url)
    if one:
        pick = [i for i in items if f"ZrqvnVq={one.group(1)}" in i["link"]]
    else:
        full = [i for i in items if "המלאה" in i["title"] or "מלאה" in i["title"]]
        full.sort(key=lambda i: (i["when"].timestamp() if i["when"] else 0), reverse=True)
        pick = full[:want]
    print(f"::notice title=RADIO::{show} | {len(items)} items | picked {len(pick)}", flush=True)
    if not pick:
        send_mail(f"⚠️ לא נמצאה תוכנית מלאה: {show}",
                  "<p>לא מצאתי בפיד של התוכנית פרק מלא" + (" עם הקישור הזה" if one else "") + ". "
                  "אפשר לשלוח קישור לפרק מסוים מתוך 'תוכניות מלאות' באתר.</p>")
        return
    folder = radio_folder(show, not one or all("מלאה" in i["title"] for i in pick))
    run(["rclone", "mkdir", f"gdrive:{folder}"])
    have = " ".join(f.get("Name", "") for f in json.loads(run(["rclone", "lsjson", "--files-only", f"gdrive:{folder}"]) or "[]"))
    os.makedirs("out", exist_ok=True)
    done, skipped, failed = [], [], []
    for it in pick:
        name = safe_name(f"{show} - {it['title']}")[:110] + ".mp3"
        if name in have:
            skipped.append(it["title"])
            continue
        path = os.path.join("out", name)
        ok = False
        for px in [""] + ([IL.replace("socks5://", "")] if IL else []):
            r = subprocess.run(["curl", "-sSL", "-m", "1800", "--retry", "2", "-o", path]
                               + (["--socks5-hostname", px] if px else []) + [it["mp3"]], capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 100_000:
                ok = True
                break
            print(f"::warning title=RADIO dl::{'IL' if px else 'direct'} {r.stderr[-200:]}", flush=True)
        if not ok:
            failed.append(it["title"])
            continue
        mb = os.path.getsize(path) / 1024 / 1024
        print(f"::notice title=RADIO file::{mb:.0f}MB {name[:80]}", flush=True)
        run(["rclone", "move", path, f"gdrive:{folder}"])
        done.append(f"{it['title']} ({mb:.0f}MB)")
    flink = drive_link_html(folder, True)
    rows = "".join(f"<li>{html.escape(n)}</li>" for n in done)
    extra = (f"<p>{len(skipped)} כבר היו בתיקייה.</p>" if skipped else "") + \
            (f"<p style='color:#a15c00'>{len(failed)} לא ירדו (אפשר לנסות שוב).</p>" if failed else "")
    send_mail(f"📻 {show}: ירדו {len(done)} תוכניות" if len(done) != 1 else f"📻 {show}: {pick[0]['title']}",
              f"<p>התוכניות המלאות עלו כ-MP3 לתיקייה <b>{html.escape(folder)}</b> בדרייב.</p><ul>{rows}</ul>{extra}{flink}"
              "<p style='color:#777;font-size:12px'>טיפ: קישור לתוכנית = הפרק המלא האחרון. להוסיף ‎.5 בסוף = 5 האחרונים.</p>")


def do_site(url: str) -> None:
    if RADIO_RE.search(url):
        do_radio(url)
        return
    if SOCIAL_RE.search(url):
        do_social(url)
        return
    raw = os.environ.get("QUERY", "") + " " + os.environ.get("BODY", "")
    max_h = 480 if re.search(r"\.480\b", raw) else 720 if re.search(r"\.720\b", raw) else 1080
    if not os.environ.get("IL_PROXY"):
        print("::warning title=SITE::no Israeli tunnel", flush=True)
        send_mail("⚠️ השרת בישראל לא זמין כרגע",
                  "<p>אתרים ישראליים (מאקו, 13 וכו') חוסמים הורדות מחוץ לישראל, וההורדה עוברת דרך השרת בירושלים - "
                  "אבל הוא <b>לא מגיב כרגע</b> (כנראה כבוי, או שהכתובת שלו השתנתה).</p>"
                  "<p>כדאי לבקש מבעל השרת לבדוק שהשרת דולק ושהכתובת לא השתנתה. כשהוא חוזר - שלח שוב את הקישור.</p>"
                  f"<p dir='ltr' style='color:#777;font-size:12px'>{html.escape(url)}</p>")
        return
    import site_dl
    r = site_episode(url, max_h, SITE_FOLDER)
    episode_page = bool(site_dl.EPISODE_RE.search(re.sub(r"[?#].*", "", url).rstrip("/") + ("/" if "/allshows/" in url or "/item/" in url else "")))
    if not episode_page and (r["why"] == "list" or (r["why"] in ("none", "live") and len(site_dl.episode_links(url, r["links"])[0]) >= 2)):
        do_site_list(url, r["title"], r["links"], max_h)
        return
    if r["why"]:
        msg = {"live": "בדף הזה יש רק שידור חי. אפשר להוריד רק פרקים או סרטונים מוקלטים - שלח קישור לעמוד של הפרק עצמו.",
               "drm": "הסרטון בדף הזה מוצפן (DRM), ואי אפשר להוריד אותו.",
               "none": "לא מצאתי סרטון בדף הזה. נסה לשלוח קישור לעמוד של הפרק עצמו (העמוד שבו הנגן מתחיל לנגן), "
                       "או לדף שמציג את רשימת הפרקים."}[r["why"]]
        send_mail(f"⚠️ לא הורד: {r['title'][:60]}", f"<p>{msg}</p><p dir='ltr' style='color:#777;font-size:12px'>{html.escape(url)}</p>")
        return
    link_html = f'<p><a href="{r["link"]}">פתח בדרייב</a></p>' if r["link"] else ""
    send_mail(f"📁 עלה לדרייב: {r['name']}",
              f"<p>הקובץ <b>{html.escape(r['name'])}.{'mp3' if os.environ.get('AUDIO_ONLY') else 'mp4'}</b> ({r['size']:.0f}MB, {r['mins']} דקות) עלה לתיקייה "
              f"<b>{html.escape(SITE_FOLDER)}</b> בדרייב שלך.</p><p>איכות: <b>{'שמע בלבד' if os.environ.get('AUDIO_ONLY') else str(r['got']) + 'p'}</b>"
              f" (הגבוהה ביותר שהאתר מציע: {r['max']}p)</p>{link_html}")


def do_site_list(url: str, title: str, links: list, max_h: int) -> None:
    """דף רשימת פרקים: מוריד את כל הפרקים לתיקייה אחת. פרק שכבר נמצא בתיקייה מדולג (ממשיך מאיפה שעצר)."""
    import time
    import site_dl
    start = time.time()
    eps, pages = site_dl.episode_links(url, links)
    for pg in pages[:15]:  # עמודים נוספים של הרשימה (page/2, page/3...)
        more: list = []
        try:
            site_dl.find_streams(pg, links_out=more, play=False)
        except Exception as err:  # noqa: BLE001
            print(f"::warning title=SITE page::{pg} {str(err)[:120]}", flush=True)
        for e in site_dl.episode_links(url, more)[0]:
            if all(e.split("?")[0].rstrip("/") != x.split("?")[0].rstrip("/") for x in eps):
                eps.append(e)
    eps.reverse()  # האתרים מציגים מהחדש לישן - מורידים מהישן לחדש
    limit = int(os.environ.get("SITE_MAX", "0") or 0)
    if limit:
        eps = eps[:limit]
    folder = f"{SITE_FOLDER}/{title}"
    run(["rclone", "mkdir", f"gdrive:{folder}"])
    have = " ".join(f.get("Name", "") for f in json.loads(run(["rclone", "lsjson", "--files-only", f"gdrive:{folder}"]) or "[]"))
    tag_of = lambda e: re.sub(r"[^\w-]", "", e.split("?")[0].rstrip("/").split("/")[-1].replace(".htm", ""))[-40:]
    todo = [e for e in eps if f"[{tag_of(e)}]" not in have]
    print(f"::notice title=SITE list::{len(eps)} episodes, {len(eps) - len(todo)} already in Drive, {len(pages)} extra pages", flush=True)
    done, failed, left = [], [], []
    for i, e in enumerate(todo):
        if (time.time() - start) / 60 > SITE_BUDGET_MIN:
            left = todo[i:]
            break
        try:
            r = site_episode(e, max_h, folder, tag_of(e))
            (done if not r["why"] else failed).append(r.get("name") or r["title"])
        except Exception as err:  # noqa: BLE001
            print(f"::warning title=SITE episode::{e} {str(err)[-200:]}", flush=True)
            failed.append(e.split("?")[0].rstrip("/").split("/")[-1])
    flink = drive_link_html(folder, True)
    rows = "".join(f"<li>{html.escape(n)}</li>" for n in done)
    more_txt = (f"<p><b>נשארו עוד {len(left)} פרקים.</b> שלח שוב את אותו קישור כדי להמשיך מאיפה שעצר.</p>" if left else "")
    fail_txt = (f"<p style='color:#a15c00'>{len(failed)} פרקים לא ירדו (אפשר לנסות שוב באותו קישור).</p>" if failed else "")
    if not eps:
        send_mail(f"⚠️ לא נמצאו פרקים: {title[:60]}",
                  "<p>לא מצאתי בדף הזה קישורים לפרקים. שלח קישור לדף שמציג את רשימת הפרקים, או לעמוד של פרק אחד.</p>")
        return
    send_mail(f"📁 {title}: ירדו {len(done)} פרקים",
              f"<p>מתוך {len(eps)} פרקים בדף: {len(eps) - len(todo)} כבר היו בתיקייה, ועכשיו ירדו <b>{len(done)}</b> "
              f"לתיקייה <b>{html.escape(folder)}</b> בדרייב.</p>{more_txt}{fail_txt}<ol>{rows}</ol>{flink}")


# ---------- תגיות וספוטיפיי ----------
TAG_NOTE = ""  # שורה למייל: מה נמצא (אמן / אלבום)


def safe_name(t: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", t or "").strip()[:90] or "שיר"


def tag_and_rename(path: str, title: str = "", artist: str = "", extra: dict | None = None) -> str:
    """מתייג שיר (אמן, אלבום, עטיפה...) ומשנה את שם הקובץ ל'אמן - שם'. אם אין התאמה - משאיר כמו שהוא."""
    global TAG_NOTE
    import tagger
    try:
        meta = tagger.tag_file(path, title, artist)
        if not meta and extra:  # בלי התאמה ב-iTunes: לפחות הפרטים מספוטיפיי
            tagger.write_tags(path, extra)
            meta = extra
        elif meta and extra:  # מספוטיפיי: שם/אמן/אלבום/מספר של ספוטיפיי גוברים
            meta = {**meta, **{k: v for k, v in extra.items() if v}}
            tagger.write_tags(path, meta)
    except Exception as err:  # noqa: BLE001
        print(f"::warning title=TAG::{str(err)[:200]}", flush=True)
        return path
    if not meta:
        print(f"::notice title=TAG::no match {os.path.basename(path)[:60]}", flush=True)
        return path
    TAG_NOTE = (f"<p style='color:#555;font-size:13px'>🏷️ {html.escape(meta.get('artist') or '')} · "
                f"{html.escape(meta.get('album') or '')}{' · ' + str(meta['year']) if meta.get('year') else ''}</p>")
    print(f"::notice title=TAG::{meta.get('artist')} - {meta.get('title')} | {meta.get('album')}", flush=True)
    prefix = re.match(r"^(\d+ - )", os.path.basename(path))
    new = os.path.join(os.path.dirname(path), (prefix.group(1) if prefix else "")
                       + safe_name(f"{meta.get('artist')} - {meta.get('title')}") + ".mp3")
    if new != path and not os.path.exists(new):
        os.rename(path, new)
        return new
    return path


def yt_find(artist: str, title: str, ms: int) -> str:
    """מוצא ביוטיוב את השיר הכי מתאים (לפי משך הזמן מספוטיפיי)."""
    from urllib.parse import quote
    url = f"https://www.youtube.com/results?search_query={quote(f'{artist} - {title}')}&gl=IL&hl=he"
    out = run_yt(ytdlp() + ["--flat-playlist", "--print", "%(id)s\t%(duration)s", "--playlist-items", "1-8", url],
                 [None, "None@il", "nc"])
    best, best_d = "", 10 ** 9
    for line in out.splitlines():
        vid, _, dur = line.partition("\t")
        if len(vid) != 11:
            continue
        try:
            d = abs(float(dur) - ms / 1000) if ms and dur not in ("NA", "None", "") else 0
        except ValueError:
            d = 0
        if not best:
            best, best_d = vid, d
        if d < best_d and d < 20:
            best, best_d = vid, d
    return best


def sp_download(t: dict, idx: int | None, album: dict) -> str:
    """מוריד שיר אחד של ספוטיפיי מיוטיוב כ-MP3 ומתייג. מחזיר נתיב או '' אם לא נמצא."""
    vid = yt_find(t["artist"], t["title"], t["ms"])
    if not vid:
        return ""
    os.makedirs(OUT, exist_ok=True)
    stem = (f"{idx:03d} - " if idx else "") + "dl"
    run_yt(ytdlp() + ["--no-playlist", "-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0",
                      "-o", os.path.join(OUT, stem + ".%(ext)s"), f"https://www.youtube.com/watch?v={vid}"], SONG_CLIENTS)
    path = os.path.join(OUT, stem + ".mp3")
    if not os.path.exists(path):
        return ""
    extra = {"title": t["title"], "artist": t["artist"]}
    if album.get("kind") == "album":
        extra.update({"album": album["name"], "album_artist": album.get("owner"), "track": idx,
                      "tracks": len(album["tracks"]), "cover": album.get("cover")})
    return tag_and_rename(path, t["title"], t["artist"].split(",")[0], extra)


def do_spotify(link: str) -> None:
    import time
    import spotify
    start = time.time()
    kind_id = spotify.resolve(link)
    if not kind_id:
        send_mail("⚠️ קישור ספוטיפיי לא מוכר", f"<p dir='ltr'>{html.escape(link)}</p>")
        return
    info = spotify.read(*kind_id)
    print(f"::notice title=SPOTIFY::{info['kind']} '{info['name'][:60]}' {len(info['tracks'])} tracks", flush=True)
    if not info["tracks"]:
        send_mail("⚠️ לא נמצאו שירים בקישור ספוטיפיי", f"<p dir='ltr'>{html.escape(link)}</p>")
        return
    if info["kind"] == "track":  # שיר בודד - במייל (או לדרייב אם גדול)
        path = sp_download(info["tracks"][0], None, info)
        if not path:
            send_mail(f"⚠️ לא מצאתי ביוטיוב: {info['tracks'][0]['title']}", "<p>נסה לשלוח את שם השיר כחיפוש רגיל.</p>")
            return
        name, size = os.path.basename(path), os.path.getsize(path)
        if size <= MAX_ATTACH:
            send_mail(f"🎵 {name}", f"<p>השיר מצורף ({size / 1048576:.1f}MB).</p>{TAG_NOTE}", attachment=path)
        else:
            link_d = drive_upload(path)
            send_mail(f"📁 עלה לדרייב: {name}", f"<p>הקובץ גדול מדי למייל ועלה לדרייב.</p>{TAG_NOTE}"
                      + f'<p><a href="{link_d}">פתח בדרייב</a></p>')
        return
    # אלבום / פלייליסט - הכול לתיקייה אחת; שירים שכבר בתיקייה מדולגים (ממשיך מאיפה שעצר)
    folder = f"{DRIVE_FOLDER}/{safe_name(info['name'])} (Spotify)"
    run(["rclone", "mkdir", f"gdrive:{folder}"])
    have = {f.get("Name", "")[:3] for f in json.loads(run(["rclone", "lsjson", "--files-only", f"gdrive:{folder}"]) or "[]")}
    tracks = info["tracks"][:300]
    done, failed, left = 0, [], 0
    for i, t in enumerate(tracks, 1):
        if f"{i:03d}" in have:
            continue
        if (time.time() - start) / 60 > SITE_BUDGET_MIN:
            left = sum(1 for j in range(i, len(tracks) + 1) if f"{j:03d}" not in have)
            break
        try:
            path = sp_download(t, i, info)
            if path:
                run(["rclone", "move", path, f"gdrive:{folder}"])
                done += 1
            else:
                failed.append(f"{t['artist']} - {t['title']}")
        except Exception as err:  # noqa: BLE001
            print(f"::warning title=SPOTIFY track::{t['title'][:40]} {str(err)[-150:]}", flush=True)
            failed.append(f"{t['artist']} - {t['title']}")
    flink = drive_link_html(folder, True)
    more = f"<p><b>נשארו עוד {left} שירים.</b> שלח שוב את אותו קישור כדי להמשיך.</p>" if left else ""
    fail = ("<p style='color:#a15c00'>לא נמצאו ביוטיוב: " + html.escape(", ".join(failed[:15])) + "</p>") if failed else ""
    send_mail(f"🎵 {info['name']}: ירדו {done} שירים",
              f"<p>{'אלבום' if info['kind'] == 'album' else 'פלייליסט'} מספוטיפיי: {len(tracks)} שירים. "
              f"ירדו עכשיו <b>{done}</b> לתיקייה <b>{html.escape(folder)}</b>, כל שיר עם שם, אמן, אלבום ועטיפה.</p>"
              f"{more}{fail}{flink}")


# ---------- הורדה ----------
def drive_url(remote: str) -> str:
    """קישור לקובץ/תיקייה בדרייב (remote בלי gdrive:). אם לא נמצא - לתיקייה שמעליו, ובסוף לתיקיית ההורדות הראשית."""
    parts = remote.strip("/").split("/")
    for n in range(len(parts), 0, -1):
        target = "/".join(parts[:n])
        try:
            info = json.loads(subprocess.run(["rclone", "lsjson", "--stat", f"gdrive:{target}"], capture_output=True,
                                             text=True, timeout=120).stdout or "{}")
        except Exception:  # noqa: BLE001
            info = {}
        if info.get("ID"):
            return (f"https://drive.google.com/drive/folders/{info['ID']}" if info.get("IsDir")
                    else f"https://drive.google.com/file/d/{info['ID']}/view")
    return "https://drive.google.com/drive/search?q=" + urllib.parse.quote(parts[-1][:60])


def drive_link_html(remote: str, folder: bool = False) -> str:
    return f'<p><a href="{drive_url(remote)}">{"פתח את התיקייה בדרייב" if folder else "פתח בדרייב"}</a></p>'


def drive_upload(path: str) -> str:
    name = os.path.basename(path)
    run(["rclone", "copy", path, f"gdrive:{DRIVE_FOLDER}"])
    return drive_url(f"{DRIVE_FOLDER}/{name}")


def pl_folder_name(info: dict, list_id: str) -> str:
    title = info.get("title") or list_id
    owner = info.get("channel") or info.get("uploader") or ""
    if list_id.startswith("UULF") and owner:
        title = f"{owner} - סרטונים"
    elif list_id.startswith("UU") and owner:
        title = f"{owner} - הכול"
    title = re.sub(r"[\\/:*?\"<>|'`$]+", " ", title).strip()[:80] or list_id
    return f"{DRIVE_FOLDER}/{title}"


def do_playlist(list_id: str, kind: str) -> None:
    """מתכנן את הורדת הפלייליסט: מחלק לחלקים של 40, וכל חלק ירוץ במחשב נפרד (ג'וב chunk)."""
    url = f"https://www.youtube.com/playlist?list={list_id}"
    info = json.loads(run(ytdlp() + ["--flat-playlist", "-J", "--playlist-items", f"1-{PL_MAX}", url]))
    total = len([e for e in info.get("entries") or [] if e])
    if total == 0:
        raise RuntimeError("הפלייליסט ריק או חסום")
    folder = pl_folder_name(info, list_id)
    run(["rclone", "mkdir", f"gdrive:{folder}"])  # תיקייה אחת לפני שהחלקים מתחילים
    # המשך מאיפה שעצר: מדלגים על מספרים שכבר קיימים בתיקייה ("007 - שם.mp3")
    # לפי מזהה הסרטון שבשם הקובץ ("[abc123XYZ_-]") - עובד גם כשנוספים סרטונים חדשים לערוץ והמספור זז
    have, have_idx = set(), set()
    try:
        for f in json.loads(run(["rclone", "lsjson", "--files-only", f"gdrive:{folder}"]) or "[]"):
            name = f.get("Name", "")
            m = re.search(r"\[([A-Za-z0-9_-]{11})\]\.\w+$", name)
            if m:
                have.add(m.group(1))
            elif re.match(r"(\d{3,4}) - ", name):  # קבצים ישנים בלי מזהה בשם
                have_idx.add(int(name.split(" - ", 1)[0]))
    except RuntimeError as err:
        print("listing folder failed:", err, flush=True)
    ents = [e for e in info.get("entries") or [] if e]
    todo = [i for i, e in enumerate(ents, 1) if e.get("id") not in have and i not in have_idx]
    have = have | have_idx
    if not todo:
        send_mail(f"📁 הפלייליסט כבר שלם בדרייב: {folder.rsplit('/', 1)[-1]}",
                  f"<p>כל {total} הפריטים כבר נמצאים בתיקייה <b>{html.escape(folder)}</b>. לא הורדתי כלום מחדש.</p>"
                  + drive_link_html(folder, True))
        return
    if have:
        print(f"::notice title=RESUME::{len(have)} already in Drive, {len(todo)} left", flush=True)
    chunks = [",".join(str(i) for i in todo[a:a + PL_CHUNK]) for a in range(0, len(todo), PL_CHUNK)]
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        raise RuntimeError("GITHUB_OUTPUT missing")
    with open(out, "a") as f:
        f.write(f"chunks={json.dumps(chunks)}\npl_id={list_id}\npl_kind={kind}\n"
                f"pl_folder={folder}\npl_total={total}\n")
    print(f"::notice title=PLAYLIST::{total} items in {len(chunks)} chunks", flush=True)


def do_plchunk() -> None:
    """מוריד חלק אחד מהפלייליסט; כל קובץ עולה מיד לדרייב ונמחק מהשרת."""
    list_id, kind = os.environ["PL_ID"], os.environ["PL_KIND"]
    rng, folder = os.environ["PL_RANGE"], os.environ["PL_FOLDER"]
    url = f"https://www.youtube.com/playlist?list={list_id}"
    os.makedirs(OUT, exist_ok=True)
    tmpl = os.path.join(OUT, "%(playlist_index)03d - %(title).70B [%(id)s].%(ext)s")
    common = ytdlp() + ["--yes-playlist", "--playlist-items", rng, "--ignore-errors", "--sleep-interval", "2",
                        "--windows-filenames", "--download-archive", "pl-archive.txt", "-o", tmpl,
                        "--exec", (f"python tagger.py {{}} ; " if kind == "plsong" else "") + f"rclone move {{}} 'gdrive:{folder}'"]
    if kind == "plsong":
        args = common + ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0", "--embed-metadata", url]
    else:
        h = {"plv480": 480, "plv720": 720}.get(kind, 1080)
        args = common + ["-f", "bv*+ba/b", "-S", f"res:{h},vcodec:h264,acodec:aac",
                         "--merge-output-format", "mp4", url]
    try:
        # בפלייליסט: אחרי הניסיון הרגיל, מיד דרך ישראל (סרטונים חסומים מחוץ לישראל), ורק אז לקוחות אחרים.
        # פריטים שכבר ירדו מדולגים (pl-archive), כך שכל סבב מנסה רק את מה שנכשל
        run_yt(args, [None, "None@il", "web_safari", "mweb", "nc", "nc@il"])
    except RuntimeError as err:
        print("some items failed:", str(err)[-300:], flush=True)
    done = len(open("pl-archive.txt").read().split("\n")) - 1 if os.path.exists("pl-archive.txt") else 0
    print(f"::notice title=CHUNK {rng}::{done} done", flush=True)


def do_plsummary() -> None:
    folder, total, kind = os.environ["PL_FOLDER"], int(os.environ.get("PL_TOTAL") or 0), os.environ["PL_KIND"]
    files = json.loads(run(["rclone", "lsjson", "--files-only", f"gdrive:{folder}"]) or "[]")
    done = len(files)
    link = ""
    try:
        link = drive_url(folder)
    except Exception as err:  # noqa: BLE001
        print("folder link failed:", err)
    title = folder.rsplit("/", 1)[-1]
    what = "שירים" if kind == "plsong" else f"סרטונים ({kind[3:]}p)"
    missing = max(0, total - done)
    send_mail(f"📁 פלייליסט עלה לדרייב: {title}",
              f"<p>בתיקייה <b>{html.escape(folder)}</b> יש עכשיו <b>{done}</b> {what} מתוך {total}.</p>"
              + (f"<p style='color:#a15c00'>{missing} פריטים לא ירדו (חסומים, פרטיים או שנמחקו).</p>" if missing else "")
              + (f'<p><a href="{link}">פתח את התיקייה בדרייב</a></p>' if link else ""))


def do_towav(spec: str) -> None:
    """המרת שירים קיימים בדרייב ל-WAV (16bit / 44.1kHz, מה שמיקסרים מקבלים) לתת-תיקייה WAV - בלי להוריד מחדש.
    spec = "ביטוי;ביטוי" (תיקיות בתוך תיקיית ההורדות שהשם שלהן מכיל אחד מהם),
    או "id:<מזהה תיקייה בדרייב>|ביטוי;ביטוי" (תיקיות בתוך תיקייה שהמשתמש יצר בעצמו, למשל 'שייקי'; ביטוי ריק = כולן)."""
    out = ""  # ";out=שם" = תיקייה מקבילה בתוך תיקיית ההורדות, ובה כל תיקייה בשם "<שם> WAV"
    m_out = re.search(r";?out=([^;|]+)", spec)
    if m_out:
        out, spec = m_out.group(1).strip(), spec.replace(m_out.group(0), "")
    base = f"gdrive:{DRIVE_FOLDER}/"
    if spec.startswith("id:"):
        fid, _, spec = spec[3:].partition("|")
        base = f"gdrive,root_folder_id={fid}:"
    words = [w.strip() for w in spec.split(";") if w.strip()]
    dirs = json.loads(run(["rclone", "lsjson", "--dirs-only", base]) or "[]")
    chosen = [d["Name"] for d in dirs if d["Name"] != "WAV" and not d["Name"].endswith(" WAV") and (not words or any(w.lower() in d["Name"].lower() for w in words))]
    print(f"::notice title=TOWAV dirs::{len(dirs)} seen, {len(chosen)} chosen", flush=True)
    report = []
    for name in chosen:
        src = f"{base}{name}"
        dst = f"gdrive:{DRIVE_FOLDER}/{out}/{name} WAV" if out else f"{src}/WAV"
        if out:  # קבצי WAV שכבר נוצרו בתת-תיקייה WAV - מעבירים לתיקייה המקבילה במקום להמיר שוב
            subprocess.run(["rclone", "move", f"{src}/WAV", dst, "--delete-empty-src-dirs"], capture_output=True)
            subprocess.run(["rclone", "rmdir", f"{src}/WAV"], capture_output=True)
        try:
            have = {f["Name"] for f in json.loads(run(["rclone", "lsjson", "--files-only", dst]) or "[]")}
        except RuntimeError:
            have = set()
        work = os.path.join(OUT, "wav")
        subprocess.run(["rm", "-rf", work])
        os.makedirs(work, exist_ok=True)
        run(["rclone", "copy", src, work, "--include", "*.mp3", "--max-depth", "1"])
        n = 0
        for mp3 in sorted(glob.glob(os.path.join(work, "*.mp3"))):
            wav = mp3[:-4] + ".wav"
            if os.path.basename(wav) not in have:
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", mp3, "-map", "0:a", "-c:a", "pcm_s16le",
                                "-ar", "44100", "-ac", "2", wav], check=False)
                n += os.path.exists(wav)
            os.remove(mp3)
        if n:
            run(["rclone", "move", work, dst, "--include", "*.wav"])
        report.append(f"<li>{html.escape(name)}: {n} קבצי WAV חדשים</li>")
        print(f"::notice title=TOWAV::{name[:50]} +{n}", flush=True)
    send_mail("🎚️ ההמרה ל-WAV הסתיימה",
              (f"<p>בתיקייה <b>{html.escape(DRIVE_FOLDER + '/' + out)}</b> נוצרה לכל אוסף תיקייה עם הסיומת WAV" if out else
               "<p>בכל אחת מהתיקיות נוצרה תת-תיקייה <b>WAV</b>") +
              f", עם השירים בפורמט WAV (16bit, 44.1kHz):</p><ul>{''.join(report)}</ul>"
              + drive_link_html(f"{DRIVE_FOLDER}/{out}" if out else DRIVE_FOLDER, True)
              if report else "<p>לא נמצאו תיקיות מתאימות.</p>")


def do_download() -> None:
    vid = os.environ.get("VID", "").strip()
    kind = os.environ.get("KIND", "").strip()
    sig = os.environ.get("SIG", "").strip()
    if kind == "pbkey":  # שינוי סולם בפלייבק
        do_pbkey()
        return
    if kind in ("plsong", "plv480", "plv720", "plv1080"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,64}", vid):
            sys.exit("bad playlist id")
        trusted = os.environ.get("TRUSTED") == "true"
        if not trusted and (not SECRET or not hmac.compare_digest(sig, sign(vid, kind))):
            sys.exit("bad signature – הקישור לא נוצר על ידי הסוכן")
        do_playlist(vid, kind)
        return
    if kind == "towav" and hmac.compare_digest(sig, sign(vid, kind)):  # המרת שירים קיימים בדרייב ל-WAV
        do_towav(vid)
        return
    if kind == "migrate" and hmac.compare_digest(sig, sign(vid, kind)):  # העברת התיקיות הישנות לתוך "הורדות" (פעם אחת)
        run(["rclone", "mkdir", "gdrive:הורדות"])
        for old_name in vid.split(";"):
            r = subprocess.run(["rclone", "moveto", f"gdrive:{old_name}", f"gdrive:הורדות/{old_name}", "-v"], capture_output=True, text=True)
            print(f"::notice title=MIGRATE::{old_name} rc={r.returncode} {(r.stderr or '')[-300:]}", flush=True)
        send_mail("📁 כל התיקיות עברו לתיקייה 'הורדות'",
                  "<p>מעכשיו כל ההורדות נשמרות בתוך התיקייה <b>הורדות</b> בדרייב:</p><ul>"
                  + "".join(f"<li>{html.escape(n)}</li>" for n in vid.split(";")) + "</ul>" + drive_link_html("הורדות", True))
        return
    if kind == "mvradio" and hmac.compare_digest(sig, sign(vid, kind)):  # תיקיות רדיו קיימות -> "הורדות רדיו" (פעם אחת)
        run(["rclone", "mkdir", f"gdrive:{RADIO_FOLDER}"])
        r = subprocess.run(["rclone", "lsjson", "--dirs-only", f"gdrive:{SITE_FOLDER}"], capture_output=True, text=True)
        moved = []
        for d in json.loads(r.stdout or "[]"):
            fl = json.loads(subprocess.run(["rclone", "lsjson", "--files-only", f"gdrive:{SITE_FOLDER}/{d['Path']}"],
                                           capture_output=True, text=True).stdout or "[]")
            if fl and all(f["Name"].lower().endswith(".mp3") for f in fl):  # רק תיקיות שכולן MP3 = תוכניות רדיו
                rr = subprocess.run(["rclone", "moveto", f"gdrive:{SITE_FOLDER}/{d['Path']}", f"gdrive:{RADIO_FOLDER}/{d['Path']}"],
                                    capture_output=True, text=True)
                print(f"::notice title=MVRADIO::{d['Path']} ({len(fl)}) rc={rr.returncode} {(rr.stderr or '')[-200:]}", flush=True)
                if rr.returncode == 0:
                    moved.append(f"{d['Path']} ({len(fl)})")
        send_mail("📻 תיקיית רדיו נפרדת",
                  "<p>מעכשיו תוכניות מ-103 ומגלי ישראל נשמרות בתיקייה <b>הורדות רדיו</b>.</p>"
                  + ("<p>העברתי לשם את התיקיות שכבר היו:</p><ul>" + "".join(f"<li>{html.escape(m)}</li>" for m in moved) + "</ul>"
                     if moved else "") + drive_link_html(RADIO_FOLDER, True))
        return
    if kind == "radiosplit" and hmac.compare_digest(sig, sign(vid, kind)):  # פעם אחת: חלוקה למלאות/קטעים + שיתוף לפי תוכנית
        full_root, parts_root = f"{RADIO_FOLDER}/תוכניות מלאות", f"{RADIO_FOLDER}/קטעים"
        run(["rclone", "mkdir", f"gdrive:{full_root}"])
        run(["rclone", "mkdir", f"gdrive:{parts_root}"])
        for d in json.loads(subprocess.run(["rclone", "lsjson", "--dirs-only", f"gdrive:{RADIO_FOLDER}"], capture_output=True, text=True).stdout or "[]"):
            if d["Path"] in ("תוכניות מלאות", "קטעים"):
                continue
            rr = subprocess.run(["rclone", "moveto", f"gdrive:{RADIO_FOLDER}/{d['Path']}", f"gdrive:{full_root}/{d['Path']}"], capture_output=True, text=True)
            print(f"::notice title=SPLIT::{d['Path']} rc={rr.returncode}", flush=True)
        root_id = drive_id(RADIO_FOLDER)
        report = []
        for key, u in USERS.items():
            if not u.get("only"):
                continue
            for sc in SHORTCUTS:
                if sc[2] in u["only"] and sc[1] in ("103", "gly"):
                    show = station_items(sc[1], sc[2])[0]
                    folder = radio_folder(show, True)
                    run(["rclone", "mkdir", f"gdrive:{folder}"])
                    ok = drive_share(folder, u["mail"])
                    report.append(f"{u['mail']}: {show} {'✓' if ok else '✗'}")
            if root_id:
                try:
                    drive_unshare(root_id, u["mail"])
                except Exception as e:  # noqa: BLE001
                    body = e.read().decode()[:400] if hasattr(e, "read") else ""
                    print(f"::warning title=UNSHARE::{u['mail']}: {e} {body}", flush=True)
                    report.append(f"{u['mail']}: לא הצלחתי להסיר את השיתוף של התיקייה הראשית")
        print("::notice title=RADIOSPLIT::" + " | ".join(report), flush=True)
        send_mail("📻 תיקיית הרדיו חולקה",
                  "<p>בתוך <b>הורדות רדיו</b> יש עכשיו <b>תוכניות מלאות</b> (תיקייה לכל תוכנית) ו<b>קטעים</b>.</p>"
                  "<p>שיתופים (רק תיקיות התוכניות שלהם):</p><ul>" + "".join(f"<li>{html.escape(r)}</li>" for r in report) + "</ul>"
                  + drive_link_html(RADIO_FOLDER, True))
        return
    if kind == "shareshows" and hmac.compare_digest(sig, sign(vid, kind)):  # יצירת תיקיות התוכניות ושיתופן
        report = [r for k in SHOW_FOLDERS for r in share_show_folder(k)]
        print("::notice title=SHARESHOWS::" + " | ".join(report), flush=True)
        return
    if kind == "share" and hmac.compare_digest(sig, sign(vid, kind)):  # שיתוף תיקייה (שנוצרה ע"י הסוכן) עם מייל, לקריאה
        import urllib.request
        path, _, email = vid.partition("|")
        if path.startswith("id:"):
            info = {"ID": path[3:]}
            subprocess.run(["rclone", "lsd", "gdrive:"], capture_output=True, text=True)  # מרענן את ה-token
        else:
            r = subprocess.run(["rclone", "lsjson", "--stat", f"gdrive:{path}"], capture_output=True, text=True)
            info = json.loads(r.stdout or "{}")
            if not info.get("ID"):
                sys.exit("share: folder not found " + (r.stderr or "")[-300:])
        conf = open(os.path.expanduser("~/.config/rclone/rclone.conf"), encoding="utf-8").read()  # ה-token רוענן ע"י rclone
        tok = json.loads(re.search(r"^token = (.*)$", conf, re.M).group(1))["access_token"]
        req = urllib.request.Request(f"https://www.googleapis.com/drive/v3/files/{info['ID']}/permissions?sendNotificationEmail=false",
                                     json.dumps({"role": "reader", "type": "user", "emailAddress": email}).encode(),
                                     {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        print(f"::notice title=SHARE::{path} -> {email}: {urllib.request.urlopen(req, timeout=60).read().decode()[:300]}", flush=True)
        return
    if kind == "setup" and hmac.compare_digest(sig, sign(vid, kind)):  # יצירת התיקיות של משתמש (פעם אחת)
        for f in (DRIVE_FOLDER, SITE_FOLDER):
            run(["rclone", "mkdir", f"gdrive:{f}"])
        print(f"::notice title=SETUP::{DRIVE_FOLDER} | {SITE_FOLDER}", flush=True)
        return
    if kind in ("q12", "q13", "q103", "qgly", "last"):  # חיפוש באתר אחר / התוכנית האחרונה מתוך כפתור במייל
        if not SECRET or not hmac.compare_digest(sig, sign(vid, kind)):
            sys.exit("bad signature – הקישור לא נוצר על ידי הסוכן")
        if kind == "last":
            st, name, au = (vid.split("|") + ["", ""])[:3]
            do_latest(st, name, au == "mp3")
        else:
            do_station_search(kind[1:], vid)
        return
    if kind in ("tv", "tv480"):  # פרק מאקו מתוך תוצאות החיפוש
        if not SECRET or not hmac.compare_digest(sig, sign(vid, kind)) or not re.match(r"https://(www\.mako\.co\.il|13tv\.co\.il)/", vid):
            sys.exit("bad signature – הקישור לא נוצר על ידי הסוכן")
        if kind == "tv480":
            os.environ["QUERY"] = vid + " .480"
        do_site(vid)
        return
    if kind == "radio":  # פרק מתוכנית רדיו (103 / גלי ישראל) מתוך תוצאות החיפוש
        if not SECRET or not hmac.compare_digest(sig, sign(vid, kind)):
            sys.exit("bad signature – הקישור לא נוצר על ידי הסוכן")
        do_radio_item(vid)
        return
    signed_kind = kind
    force = kind.endswith("!")  # כפתור "להוריד שוב בכל זאת"
    kind = kind.rstrip("!")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid) or kind not in ("song", "video", "v480", "v720", "v1080"):
        sys.exit("bad id/kind")
    # הפעלה ידנית מלשונית Actions אפשרית רק לבעל המאגר, לכן אין צורך בחתימה
    trusted = os.environ.get("TRUSTED") == "true"
    if not trusted and (not SECRET or not hmac.compare_digest(sig, sign(vid, signed_kind))):
        sys.exit("bad signature – הקישור לא נוצר על ידי הסוכן")

    # לחיצה שנייה על אותו כפתור (גם אחרי ימים) - לא מורידים שוב, אלא מזכירים איפה הקובץ
    led_key = f"click|{USER_KEY}|{vid}|{kind}"
    prev = None if force else ledger_read().get(led_key)
    if prev:
        in_drive = prev.get("folder") and prev.get("name") in folder_files(prev["folder"])
        if in_drive or (prev.get("mail") and time.time() - prev.get("ts", 0) < 30 * 86400):
            print(f"::notice title=ALREADY::{led_key} ({prev.get('at')})", flush=True)
            where = (f"<p>הקובץ <b>{html.escape(prev['name'])}</b> כבר הורד ב-{prev.get('at', '')} לתיקייה "
                     f"<b>{html.escape(prev['folder'])}</b>, אז לא הורדתי אותו שוב.</p>"
                     + drive_link_html(prev["folder"] + "/" + prev["name"]) + drive_link_html(prev["folder"], True)) if in_drive else (
                     f"<p>השיר <b>{html.escape(prev.get('name', ''))}</b> כבר נשלח אליך במייל ב-{prev.get('at', '')}, "
                     "אז לא הורדתי אותו שוב. (חפש במייל לפי שם השיר.)</p>")
            send_mail(f"✅ כבר הורד: {prev.get('name', vid)[:70]}",
                      where + "<p style='color:#777'>כנראה נלחץ שוב כפתור במייל ישן. אם בכל זאת צריך עוד עותק:</p>"
                      + click_button(vid, kind + "!", "⬇️ להוריד שוב בכל זאת"))
            return

    url = f"https://www.youtube.com/watch?v={vid}"
    os.makedirs(OUT, exist_ok=True)
    tmpl = os.path.join(OUT, "%(title).90B.%(ext)s")
    common = ytdlp() + ["--no-playlist", "--windows-filenames", "-o", tmpl]
    if kind == "song":
        run_yt(common + ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0",
                         "--embed-metadata", "--embed-thumbnail", url], SONG_CLIENTS)
        files = glob.glob(os.path.join(OUT, "*.mp3"))
        if files:
            files = [tag_and_rename(files[0])]
    else:
        h = {"v480": 480, "v720": 720}.get(kind, 1080)  # "video" (קישורים ישנים) = 1080
        # הרזולוציה הגבוהה ביותר עד h, עדיפות ל-H.264/AAC (מתנגן בכל מכשיר), מאוחד ל-MP4
        run_yt(common + ["-f", "bv*+ba/b", "-S", f"res:{h},vcodec:h264,acodec:aac",
                         "--merge-output-format", "mp4", url])
        files = glob.glob(os.path.join(OUT, "*.mp4"))
    if not files:
        raise RuntimeError("ההורדה הסתיימה אבל לא נמצא קובץ")
    path = files[0]
    name = os.path.basename(path)
    size_mb = os.path.getsize(path) / 1024 / 1024

    if kind == "song" and os.path.getsize(path) <= MAX_ATTACH:
        send_mail(f"🎵 {name}", f"<p>השיר מצורף ({size_mb:.1f}MB).</p>{TAG_NOTE}", attachment=path)
        ledger_write(led_key, {"name": name, "mail": True, "ts": time.time(), "at": time.strftime("%d.%m.%Y")})
        return

    quality = ""
    if kind != "song":
        try:
            got = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                  "stream=height,codec_name", "-of", "csv=p=0", path],
                                 capture_output=True, text=True).stdout.strip()
            codec, height = (got.split(",") + [""])[:2]
            print(f"::notice title=VIDEO::{vid} {height}p {codec} {size_mb:.0f}MB (requested {kind})", flush=True)
            want = {"v480": 480, "v720": 720}.get(kind, 1080)
            quality = f"<p>איכות: <b>{height}p</b></p>"
            if height.isdigit() and int(height) < want:
                quality += f"<p style='color:#a15c00'>זו האיכות הגבוהה ביותר שהייתה זמינה לסרטון הזה (ביקשת {want}p).</p>"
                if FALLBACK_NOTES:
                    quality += ("<p style='color:#999;font-size:12px' dir='ltr'>"
                                + html.escape(" / ".join(FALLBACK_NOTES))[:500] + "</p>")
        except Exception as err:  # noqa: BLE001
            print("ffprobe failed:", err)

    link = drive_upload(path)
    ledger_write(led_key, {"folder": DRIVE_FOLDER, "name": name, "ts": time.time(), "at": time.strftime("%d.%m.%Y")})
    reason = "" if kind != "song" else "<p>השיר גדול מדי למייל, לכן הוא עלה לדרייב.</p>"
    link_html = f'<p><a href="{link}">פתח בדרייב</a></p>' if link else ""
    send_mail(f"📁 עלה לדרייב: {name}",
              f"{reason}<p>הקובץ <b>{html.escape(name)}</b> ({size_mb:.0f}MB) עלה לתיקייה "
              f"<b>{html.escape(DRIVE_FOLDER)}</b> בדרייב שלך.</p>{quality}{link_html}")


# ---------- פלייבק: שיר מצורף במייל -> אותו שיר בלי ווקל ----------
# המודל: Demucs htdemucs - אותו מודל של "הסרת קול AI" בקריוקי באתר נווה שלום.
# המייל נשלח לכתובת של Make, ולכן הקובץ לא עובר ב-Make (מגבלת 5MB): הסוכן קורא אותו
# ישירות מתיבת הג'ימייל של השולח (דואר יוצא) דרך IMAP, עם אותה סיסמת אפליקציה.
MAILHOOK = os.environ.get("MAKE_MAILHOOK", "")
PB_FOLDER = "הורדות/פלייבקים"
PB_MODEL = os.environ.get("PB_MODEL", "htdemucs")
PB_PY = os.environ.get("PB_PY", "/tmp/dm/bin/python")
PB_MAX_MIN = 30  # שיר ארוך מזה - לא מפרידים (זמן ריצה)
PB_MAX_SEMI = 6  # שינוי סולם: עד 6 חצאי טונים למעלה / למטה
PB_HELP = ("<p>כדי לקבל שיר בלי הקול של הזמר:</p><ol>"
           f"<li>שולחים מייל לכתובת: <b dir='ltr'>{MAILHOOK}</b></li>"
           "<li><b>מצרפים את השיר</b> (קובץ MP3 או כל קובץ שמע אחר). נושא - לא חובה.</li>"
           f"<li><b>מוסיפים את אריה בעותק (CC):</b> <span dir='ltr'>{GMAIL_USER}</span> - בלי זה הקובץ לא מגיע.</li></ol>"
           "<p>אחרי כמה דקות (בערך כאורך השיר) מגיע מייל עם השיר בלי הקול.</p>")
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".mp4", ".webm", ".amr", ".3gp", ".mpeg")


def _imap():
    import imaplib
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(GMAIL_USER, GMAIL_PASS)
    box = '"[Gmail]/All Mail"'
    for line in (M.list()[1] or []):  # שם התיקייה "כל הדואר" תלוי בשפת החשבון - מחפשים לפי הסימון \All
        s = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
        if "\\All" in s:
            box = s.split(' "/" ')[-1].strip()
            break
    M.select(box, readonly=True)
    return M


def _hdr(v) -> str:
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:  # noqa: BLE001
        return str(v or "")


def _audio_part(msg):
    for part in msg.iter_attachments():
        name = (part.get_filename() or "").lower()
        if part.get_content_type().startswith(("audio/", "video/")) or name.endswith(AUDIO_EXT):
            return part
    return None


def _fetch_msg(M, uid: bytes):
    import email
    import email.policy
    d = M.uid("FETCH", uid, "(BODY.PEEK[])")[1]
    raw = next((x[1] for x in d if isinstance(x, tuple)), None)
    return email.message_from_bytes(raw, policy=email.policy.default) if raw else None


def find_playback_mail(query: str) -> str:
    """המייל האחרון ששלחת לסוכן (באותו נושא). אם יש בו קובץ שמע - מחזיר את מזהה ה-IMAP שלו."""
    import email
    norm = lambda t: re.sub(r"\s+", " ", re.sub(r"^\s*((re|fw|fwd|השב|העבר)\s*:\s*)+", "", t or "", flags=re.I)).strip()
    q = norm(query)
    M = _imap()
    try:
        sender = re.sub(r".*<|>.*", "", os.environ.get("FROM", "")).strip().lower()
        who = f" from:{sender}" if re.fullmatch(r"[\w.+-]+@[\w.-]+", sender) else ""
        uids = (M.uid("SEARCH", "X-GM-RAW", f'"to:{MAILHOOK}{who} newer_than:1d"')[1][0] or b"").split()[-12:]
        if not uids:
            return ""
        chosen = uids[-1]  # ברירת מחדל: המייל האחרון שנשלח לסוכן (זה שהפעיל את ההרצה)
        for uid in reversed(uids):
            d = M.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")[1]
            raw = next((x[1] for x in d if isinstance(x, tuple)), b"")
            if norm(_hdr(email.message_from_bytes(raw).get("Subject", ""))) == q:
                chosen = uid
                break
        msg = _fetch_msg(M, chosen)
        part = _audio_part(msg) if msg else None
        print(f"::notice title=PLAYBACK check::uid={chosen.decode()} audio={'yes: ' + (part.get_filename() or '') if part else 'no'}", flush=True)
        return chosen.decode() if part else ""
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


def separate(src: str, title: str) -> None:
    """מפריד את השיר עם Demucs ושולח את הפלייבק (MP3 320) במייל, או לדרייב אם הוא גדול."""
    import shutil
    title = re.sub(r"[_\s]+", " ", title).strip() or "שיר"
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
                                   capture_output=True, text=True).stdout.strip() or 0)
    except ValueError:
        dur = 0
    if not dur:
        send_mail("🎤 פלייבק: הקובץ לא נפתח", f"<p>לא הצלחתי לפתוח את הקובץ <b>{html.escape(title)}</b> כשיר. נסה לשלוח MP3 או WAV.</p>")
        return
    if dur > PB_MAX_MIN * 60:
        send_mail("🎤 פלייבק: הקובץ ארוך מדי",
                  f"<p>הקובץ <b>{html.escape(title)}</b> ארוך {fmt_dur(dur)}. אני מכין פלייבק לשירים עד {PB_MAX_MIN} דקות.</p>")
        return
    t0 = time.time()
    os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 2))  # כל הליבות של המחשב
    run([PB_PY, "-m", "demucs", "-n", PB_MODEL, "--two-stems=vocals", "--mp3", "--mp3-bitrate", "320",
         "-o", f"{OUT}/pb/sep", src])
    stem = os.path.splitext(os.path.basename(src))[0]
    res = f"{OUT}/pb/sep/{PB_MODEL}/{stem}/no_vocals.mp3"
    name = safe_name(title) + " - פלייבק.mp3"
    final = f"{OUT}/pb/{name}"
    shutil.move(res, final)
    took, size = (time.time() - t0) / 60, os.path.getsize(final)
    print(f"::notice title=PLAYBACK done::{title} | {fmt_dur(dur)} | {took:.1f} min | {size / 1e6:.1f}MB", flush=True)
    body = (f"<p>הנה <b>{html.escape(title)}</b> בלי הווקל.</p>"
            f"<p style='color:#666;font-size:13px'>אורך {fmt_dur(dur)} · MP3 320 · ההפרדה לקחה {took:.0f} דקות "
            f"(מודל AI ‏{PB_MODEL}, אותו מודל של הקריוקי באתר נווה שלום)</p>")
    # תמיד שומרים בדרייב: ממנו כפתורי ‎-1 / +1 מכינים את הפלייבק בסולם אחר (תמיד מהמקור, בלי איבוד איכות מצטבר)
    folder, saved = pb_folder(), False
    try:
        run(["rclone", "mkdir", f"gdrive:{folder}"])
        run(["rclone", "copy", final, f"gdrive:{folder}"])
        saved = True
        if USERS.get(USER_KEY, {}).get("pb_only"):
            drive_share(folder, USERS[USER_KEY]["mail"])
    except Exception as err:  # noqa: BLE001
        print(f"::warning title=PLAYBACK drive::{type(err).__name__}: {str(err)[-300:]}", flush=True)
    n = requested_key()
    if n:  # בגוף המייל ביקשו סולם (למשל +4): שולחים ישר בסולם הזה (המקור שמור בדרייב לכפתורים)
        send_key_version(final, f"{folder}/{name}" if saved else "", n, title,
                         f"<p style='color:#666;font-size:13px'>אורך {fmt_dur(dur)} · ההפרדה לקחה {took:.0f} דקות</p>")
        return
    keys = pb_key_buttons(f"{folder}/{name}", 0) if saved else ""
    if size <= MAX_ATTACH:
        send_mail(f"🎤 פלייבק: {title}", body + keys, final)
    elif saved:
        send_mail(f"🎤 פלייבק: {title}", body + "<p>הקובץ גדול מדי למייל, אז שמרתי אותו בדרייב:</p>"
                  + drive_link_html(f"{folder}/{name}") + keys)
    else:
        raise RuntimeError("הפלייבק גדול מדי למייל והשמירה בדרייב נכשלה")


def pb_folder() -> str:
    return USERS[USER_KEY]["yt"] if USERS.get(USER_KEY, {}).get("pb_only") else PB_FOLDER


def semi_text(n: int) -> str:
    a = abs(n)
    if not n:
        return "הסולם המקורי"
    amount = "חצי טון" if a == 1 else "טון" if a == 2 else f"{a / 2:g} טונים" if a % 2 == 0 else f"{a} חצאי טונים"
    return f"{amount} {'למעלה' if n > 0 else 'למטה'}"


def pb_key_buttons(rel: str, n: int) -> str:
    """כפתורי שינוי סולם: כל לחיצה מכינה את הפלייבק בחצי טון למטה / למעלה (ביחס לסולם של המייל הזה)."""
    btn = lambda m, label: click_button(f"{m}|{rel}", "pbkey", label, True) if abs(m) <= PB_MAX_SEMI else ""
    return ("<div style='margin-top:14px;padding:10px 12px;background:#f4f6fb;border-radius:10px'>"
            f"<b>לשנות סולם:</b> (עכשיו: {semi_text(n)})<br>"
            + btn(n - 1, "⬇️ ‎-1 (חצי טון למטה)") + btn(n + 1, "⬆️ ‎+1 (חצי טון למעלה)")
            + (click_button(f"0|{rel}", "pbkey", "↩️ חזרה לסולם המקורי") if n else "")
            + "<div style='margin-top:8px;font-size:13px'>או ישר לסולם (בחצאי טונים מהמקור):<br>"
            + "".join(_mini_key(m, rel) for m in range(-PB_MAX_SEMI, PB_MAX_SEMI + 1) if m and m != n) + "</div>"
            + "<div style='font-size:12px;color:#777;margin-top:6px'>אחרי לחיצה מגיע מייל חדש תוך דקה-שתיים. "
            "אפשר גם לבקש סולם כבר כששולחים את השיר: לכתוב בגוף המייל למשל ‎+4 או ‎-2.</div></div>")


def _mini_key(m: int, rel: str) -> str:
    params = {"id": f"{m}|{rel}", "k": "pbkey", "s": sign(f"{m}|{rel}", "pbkey")}
    if USER_KEY:
        params["u"] = USER_KEY
    return (f'<a href="{html.escape(CLICK_URL + "?" + urlencode(params))}" style="display:inline-block;margin:4px 0 0 4px;'
            f'padding:3px 8px;border-radius:10px;text-decoration:none;font-size:12px;font-weight:bold;'
            f'background:#eef2f8;color:#1a56b0" dir="ltr">{m:+d}</a>')


def requested_key() -> int:
    """סולם שביקשו בגוף המייל ששלח את השיר: ‎+4 / ‎-2 (בחצאי טונים, עד 6)."""
    body = os.environ.get("BODY", "")
    body = re.split(r"\n\s*(--\s*$|>|On .+wrote:|ב.+ כתב)", body, maxsplit=1, flags=re.M)[0]  # בלי חתימה / ציטוט
    m = re.search(r"(?<![\w\d])([+\-−–])\s*(\d{1,2})(?![\d.:/])", body)
    if not m:
        return 0
    n = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
    return max(-PB_MAX_SEMI, min(PB_MAX_SEMI, n))


def do_pbkey() -> None:
    """לחיצה על ‎-1 / +1: מוריד את הפלייבק המקורי מהדרייב, משנה סולם (בלי לשנות מהירות) ושולח."""
    vid, sig = os.environ.get("VID", ""), os.environ.get("SIG", "")
    if not SECRET or not hmac.compare_digest(sig, sign(vid, "pbkey")):
        sys.exit("bad signature – הקישור לא נוצר על ידי הסוכן")
    n_s, rel = (vid.split("|", 1) + [""])[:2]
    n = max(-PB_MAX_SEMI, min(PB_MAX_SEMI, int(n_s)))
    os.makedirs(f"{OUT}/pb", exist_ok=True)
    src = f"{OUT}/pb/orig.mp3"
    run(["rclone", "copyto", f"gdrive:{rel}", src])
    title = re.sub(r"\s*-\s*פלייבק$", "", os.path.splitext(os.path.basename(rel))[0])
    send_key_version(src, rel, n, title)


def send_key_version(src: str, rel: str, n: int, title: str, extra: str = "") -> None:
    """מכין את הפלייבק בסולם n (מהמקור, בלי לשנות מהירות) ושולח במייל עם כפתורי הסולם.
    rel = המקור בדרייב (בשביל הכפתורים); ריק אם לא נשמר."""
    base = os.path.splitext(os.path.basename(rel or src))[0]
    if n:
        r = 2 ** (n / 12)
        has_rb = "rubberband" in subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        af = f"rubberband=pitch={r:.6f}:pitchq=quality" if has_rb else f"asetrate=44100*{r:.6f},aresample=44100,atempo={1 / r:.6f}"
        name = f"{base} ({n:+d}).mp3"
        out = f"{OUT}/pb/{name}"
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af, "-ar", "44100", "-b:a", "320k", out])
        print(f"::notice title=PBKEY::{n:+d} rubberband={has_rb} {rel}", flush=True)
    else:
        name, out = os.path.basename(rel or src), src
    body = f"<p>הנה הפלייבק של <b>{html.escape(title)}</b> ב{semi_text(n)}" + (" מהמקור" if n else "") + ".</p>" + extra
    keys = pb_key_buttons(rel, n) if rel else ""
    subj = f"🎤 פלייבק {n:+d}: {title}" if n else f"🎤 פלייבק: {title}"
    if os.path.getsize(out) <= MAX_ATTACH:
        send_mail(subj, body + keys, out)
    else:
        folder = os.path.dirname(rel) if rel else pb_folder()
        run(["rclone", "copy", out, f"gdrive:{folder}"])
        send_mail(subj, body + "<p>הקובץ גדול מדי למייל, אז שמרתי אותו בדרייב:</p>"
                  + drive_link_html(f"{folder}/{name}") + keys)


def do_playback() -> None:
    os.makedirs(f"{OUT}/pb", exist_ok=True)
    if os.environ.get("ACTION") == "pbtest":  # בדיקה ידנית: קובץ שמע מקישור (בשדה query)
        import urllib.request
        url = os.environ.get("QUERY", "").strip()
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".mp3"
        src = f"{OUT}/pb/song{ext}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 yt-agent playback test"})
        with urllib.request.urlopen(req, timeout=120) as r, open(src, "wb") as f:
            f.write(r.read())
        separate(src, urllib.parse.unquote(os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0]) or "בדיקה")
        return
    M = _imap()
    try:
        msg = _fetch_msg(M, os.environ.get("PB_UID", "").encode())
    finally:
        M.logout()
    part = _audio_part(msg) if msg else None
    if not part:
        send_mail("🎤 פלייבק: לא מצאתי שיר במייל", "<p>לא מצאתי קובץ שמע מצורף. צרף את השיר (MP3 / WAV / M4A) ושלח שוב.</p>")
        return
    fname = part.get_filename() or "שיר.mp3"
    stem, ext = os.path.splitext(fname)
    src = f"{OUT}/pb/song{(ext or '.mp3').lower()}"
    with open(src, "wb") as f:
        f.write(part.get_content())
    subj = _hdr(msg.get("Subject", "")).strip()
    separate(src, stem.strip() or subj or "שיר")


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ACTION", "")
    if action == "plchunk":  # חלק של פלייליסט - שגיאה בחלק אחד לא שולחת מייל
        do_plchunk()
        return
    if action == "detect":  # לפני ההתקנות: האם זה קישור לאתר אחר (צריך דפדפן ומנהרה)
        if os.environ.get("ACTION") == "download" and os.environ.get("KIND") == "pbkey":  # ‎-1 / +1 בפלייבק: רק rclone + ffmpeg
            if os.environ.get("GITHUB_ENV"):
                with open(os.environ["GITHUB_ENV"], "a") as f:
                    f.write("PBKEY=1\n")
            print("::notice title=DETECT::playback key change", flush=True)
            return
        pb = ""
        if os.environ.get("ACTION") == "pbtest":
            pb = "test"
        elif os.environ.get("ACTION") == "search" and (not USER_KEY or USERS[USER_KEY].get("pb_only")) and GMAIL_PASS:  # שיר מצורף = פלייבק
            try:
                pb = find_playback_mail(os.environ.get("QUERY", ""))
            except Exception as err:  # noqa: BLE001
                print(f"::warning title=PLAYBACK imap::{type(err).__name__}: {err}", flush=True)
        if pb:
            if os.environ.get("GITHUB_ENV"):
                with open(os.environ["GITHUB_ENV"], "a") as f:
                    f.write(f"PLAYBACK=1\nPB_UID={pb}\nNEEDS_DRIVE=1\n")
            print("::notice title=DETECT::playback", flush=True)
            return
        if USERS.get(USER_KEY, {}).get("pb_only"):  # משתמשת פלייבק בלבד: בלי יוטיוב / אתרים / קיצורים
            print("::notice title=DETECT::playback user, no file", flush=True)
            return
        u = site_link() if os.environ.get("ACTION") == "search" and not USERS.get(USER_KEY, {}).get("only") else ""
        import spotify
        sp = os.environ.get("ACTION") == "search" and (spotify.SPOTIFY_URL.search(os.environ.get("QUERY", ""))
                                                        or spotify.SPOTIFY_URL.search(os.environ.get("BODY", "")))
        if (u or sp) and os.environ.get("GITHUB_ENV"):
            with open(os.environ["GITHUB_ENV"], "a") as f:
                if u:
                    f.write(f"SITE_URL={u}\n")
                    if SOCIAL_RE.search(u) or RADIO_RE.search(u):  # פייסבוק / 103FM: בלי דפדפן
                        f.write("SOCIAL=1\n")
                f.write("NEEDS_DRIVE=1\n")  # צריך rclone + PO Token גם בפעולת חיפוש
        st = station_marker()[0] if os.environ.get("ACTION") == "search" and not u else ""
        sc = shortcut_for(os.environ.get("QUERY", "")) if os.environ.get("ACTION") == "search" and not u else None
        if os.environ.get("ACTION") == "download" and os.environ.get("KIND") == "last":
            sc = (os.environ.get("VID", "").split("|")[0], "", "", False)
        if sc and os.environ.get("GITHUB_ENV"):
            with open(os.environ["GITHUB_ENV"], "a") as f:  # קיצור = הורדה מיידית לדרייב (ו-12/13 צריכים דפדפן)
                f.write("NEEDS_DRIVE=1\n" + ("NEEDS_BROWSER=1\n" if sc[0] in ("12", "13") else ""))
        if (st == "12" or (os.environ.get("ACTION") == "download" and os.environ.get("KIND", "") in ("tv", "tv480", "q12"))) and os.environ.get("GITHUB_ENV"):
            with open(os.environ["GITHUB_ENV"], "a") as f:
                f.write("NEEDS_BROWSER=1\nNEEDS_DRIVE=1\n")
        print(f"::notice title=DETECT::{'site link' if u else 'youtube/search'}", flush=True)
        return
    if action == "line":  # העלאה לקו נווה שלום (ימות המשיח)
        import yemot_line
        j = yemot_line.job()
        print(f"::notice title=LINE job::{j}", flush=True)
        if j["st"] == "rss":
            do_rss_line(j["name"])
        else:
            do_latest(j["st"], j["name"], True)
        return
    if USERS.get(USER_KEY, {}).get("pb_only"):  # משתמשת פלייבק בלבד
        if action == "search" and os.environ.get("PLAYBACK"):
            try:
                do_playback()
            except Exception as err:  # noqa: BLE001
                print("::error::" + f"{type(err).__name__}: {err}".replace("\n", " | ")[-1500:], flush=True)
                send_mail("🎤 פלייבק: משהו השתבש", "<p>לא הצלחתי להכין את הפלייבק הפעם. אפשר לנסות לשלוח שוב, "
                          "ואם זה חוזר - לספר לאריה.</p>")
                sys.exit(1)
        elif action == "download" and os.environ.get("KIND") == "pbkey":
            do_pbkey()
        elif action == "search":
            send_mail("🎤 פלייבק: לא מצאתי שיר במייל", PB_HELP)
        else:
            print(f"::notice title=LIMITED::{USER_KEY} blocked {action}", flush=True)
        return
    only = USERS.get(USER_KEY, {}).get("only")
    if only:  # משתמש מוגבל: רק הקיצורים שלו (בנושא / בכפתור), ותפריט
        q = os.environ.get("QUERY", "")
        sc = shortcut_for(q) if action == "search" else None
        if action == "search" and _norm(q) in ("תפריט", "קיצורים", "menu", "עזרה"):
            do_limited_menu()
            return
        if action == "search" and sc and sc[1] in only:
            do_latest(sc[0], sc[1], sc[3])
            return
        vid, kind = os.environ.get("VID", ""), os.environ.get("KIND", "")
        if action == "download" and kind == "last" and (vid.split("|") + [""])[1] in only and SECRET \
                and hmac.compare_digest(os.environ.get("SIG", ""), sign(vid, kind)):
            st, name, au = (vid.split("|") + ["", ""])[:3]
            do_latest(st, name, au == "mp3")
            return
        print(f"::notice title=LIMITED::{USER_KEY} blocked {action} {q[:60]} {kind}", flush=True)
        if action == "search":
            do_limited_menu()  # במקום לא מורשה - מקבל את רשימת התוכניות שלו
        return
    try:
        if os.environ.get("PLAYBACK") and action in ("search", "pbtest"):
            do_playback()
        elif action == "search" and os.environ.get("SITE_URL"):
            do_site(os.environ["SITE_URL"])
        elif action == "search":
            do_search()
        elif action == "download":
            do_download()
        elif action == "plsummary":
            do_plsummary()
        else:
            sys.exit(f"unknown action: {action}")
    except Exception as err:  # noqa: BLE001
        msg = f"{type(err).__name__}: {err}"
        # שורה שמופיעה כהערה בדף ההרצה ב-GitHub (קל לקרוא את השגיאה)
        print("::error::" + msg.replace("\n", " | ")[-1500:], flush=True)
        hint = ""
        if "Sign in to confirm" in msg or "bot" in msg.lower():
            hint = "<p><b>יוטיוב חסם את השרת של GitHub.</b> צריך להוסיף קובץ עוגיות (YT_COOKIES).</p>"
        try:
            send_mail("❌ סוכן הורדות: שגיאה",
                      f"{hint}<pre dir='ltr' style='font-size:12px;white-space:pre-wrap'>{html.escape(msg)}</pre>")
        finally:
            sys.exit(1)


def decode_payload() -> None:
    """Make שולח את הערכים בקידוד base64 (כדי שמרכאות בשם לא ישברו את ה-JSON)."""
    import base64
    for name in ("QUERY", "BODY", "VID", "KIND", "SIG", "LINK_SECRET", "FROM", "UKEY"):
        b64 = os.environ.get(name + "_B64", "").strip()
        if b64:
            try:
                os.environ[name] = base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8", "replace")
            except ValueError:
                pass
    global SECRET
    SECRET = os.environ.get("LINK_SECRET", SECRET)
    # מי המשתמש: בחיפוש לפי כתובת השולח, בלחיצה לפי u שבקישור (חתום)
    global USER_KEY, MAIL_TO, DRIVE_FOLDER, SITE_FOLDER, RADIO_FOLDER
    sender = re.sub(r".*<|>.*", "", os.environ.get("FROM", "")).strip().lower()
    USER_KEY = USER_BY_MAIL.get(sender, "") or (os.environ.get("UKEY", "").strip() if os.environ.get("UKEY", "").strip() in USERS else "")
    if USER_KEY:
        u = USERS[USER_KEY]
        MAIL_TO, DRIVE_FOLDER, SITE_FOLDER, RADIO_FOLDER = u["mail"], u["yt"], u["site"], u["radio"]
        print(f"::notice title=USER::{USER_KEY}", flush=True)


if __name__ == "__main__":
    decode_payload()
    try:
        main()
    except SystemExit as ex:
        if ex.code not in (None, 0):
            print(f"::error title=EXIT::{ex.code} | vid={os.environ.get('VID','')} kind={os.environ.get('KIND','')}", flush=True)
        raise
