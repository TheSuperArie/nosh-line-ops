"""מבזקים לשלוחה 8/9 (נסתרת) מהצ'אט ב-sport5.co.il/NewsRoom - אותו מנגנון של סוכן המבזקים (הקראה, סינון כפולים, שבת).
# v2 18/11: שמות קבועים + סינון כפילויות
רק טקסט - תמונות וסרטונים לא עולים. קורא את הכול פעם בחצי שעה, ובין לבין רק את מה שחדש."""
import html, os, re, subprocess, sys, time
from datetime import datetime
D = os.path.dirname(os.path.abspath(__file__))
os.environ.update(NEWS_EXT="/8/9/1", NEWS_SAY_CH="0", NEWS_KEEP="30", NEWS_STATE=os.path.join(D, "news_sport_state.json"))
sys.path.insert(0, D)
import news_agent as n  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
CJ = os.path.join(D, "sport_cookies.txt")
S = {"last": 0, "full_at": 0}


def fetch(mode, lid=None):
    url = "https://www.sport5.co.il/Ajax/GetNewsRoom.aspx?mode=" + str(mode) + (f"&lid={lid}" if lid else "")
    if not os.path.exists(CJ) or time.time() - os.path.getmtime(CJ) > 6 * 3600:
        subprocess.run(["curl", "-sL", "-m", "40", "-c", CJ, "-b", CJ, "-A", UA, "https://www.sport5.co.il/NewsRoom", "-o", "/dev/null"])
    r = subprocess.run(["curl", "-sL", "-m", "60", "--compressed", "-c", CJ, "-b", CJ, "-A", UA, "-H", "X-Requested-With: XMLHttpRequest",
                        "-H", "Referer: https://www.sport5.co.il/NewsRoom", url], capture_output=True)
    return r.stdout.decode("utf-8", "replace")


def parse(page):
    """עובר על הדף לפי הסדר: שורות תאריך ואחריהן הודעות (מזהה, שעה, כותב, טקסט)."""
    out, day = [], datetime.now(n.IL).strftime("%d.%m.%Y")
    for m in re.finditer(r'newsroom-date[^"]*" data="([\d.]+)"|<div class="container-message">(.*?)<!-- newroom control end -->', page, re.S):
        if m.group(1):
            day = m.group(1)
            continue
        blk = m.group(2)
        aid = re.search(r'hash="#nr(\d+)"', blk)
        tm = re.search(r'class="newsroom-time">\s*([\d:]+)', blk)
        nm = re.search(r'class="newsroom-name">([^<]*)', blk)
        body = re.search(r'class="content-text">(.*?)</div>\s*</div>', blk, re.S)
        spans = re.findall(r"<span[^>]*>(.*?)</span>", body.group(1), re.S) if body else []
        txt = n.clean(html.unescape(" ".join(spans)))
        if not aid or len(txt) < 15:  # רק תמונה / סרטון בלי טקסט - לא עולה
            continue
        try:
            ts = datetime.strptime(f"{day} {tm.group(1) if tm else '00:00'}", "%d.%m.%Y %H:%M").replace(tzinfo=n.IL)
        except ValueError:
            ts = datetime.now(n.IL)
        out.append({"id": int(aid.group(1)), "say": f"בשעה {ts.strftime('%H:%M')}: {txt}.", "txt": txt,
                    "ts": ts.timestamp(), "ch": html.unescape(nm.group(1)).strip() if nm else ""})
    return out


def latest(_n=0):
    full = not S["last"] or time.time() - S["full_at"] > 1800
    items = parse(fetch(2) if full else fetch(1, S["last"]))
    if full:
        S["full_at"] = time.time()
    if items:
        S["last"] = max(S["last"], max(i["id"] for i in items))
    return sorted(items, key=lambda i: i["id"], reverse=True)[:30]


n.latest = latest

if __name__ == "__main__":
    n.log("sport news agent started")
    while True:
        try:
            n.sync("poll")
        except Exception as e:  # noqa: BLE001
            n.log("sport poll error:", str(e)[:150])
        time.sleep(60)
