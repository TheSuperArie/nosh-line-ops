"""מעקב זמני פרסום: כל 5 דקות בודק מתי כל תוכנית הופיעה באתר שלה (פעם ראשונה שנראתה), כדי לדעת מתי לצפות לה בקו.
שומר ב-ivr2:/AgentCode/timing.txt (שורה לכל פרק: מקור | מתי נראה | זמן פרסום אם ידוע | כותרת). רק רושם - לא מוריד ולא מעלה כלום."""
import html, json, os, re, subprocess, time, urllib.parse, urllib.request
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "timing_state.json")
IL = ZoneInfo("Asia/Jerusalem")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
GLZ = ("https://www.omnycontent.com/d/playlist/6dcbc33f-1fb6-49de-9ae2-ad8a00c01523/"
       "71453d31-14da-43ed-ae4a-adef00df863c/755c5b83-44d1-4a8c-b88f-adef00e16066/podcast.rss")


def get(url):
    return subprocess.run(["curl", "-sSL", "-m", "60", "-A", UA, url], capture_output=True).stdout.decode("utf-8", "replace")


def ym(name, **p):
    p["token"] = KEY
    with urllib.request.urlopen(urllib.request.Request(API + name, data=urllib.parse.urlencode(p).encode()), timeout=120) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def now():
    return datetime.now(IL).strftime("%Y-%m-%d %a %H:%M")


# כל מקור מחזיר [(מזהה, כותרת, זמן פרסום אם ידוע)]
def c14():
    x = get("https://www.youtube.com/feeds/videos.xml?channel_id=UCKEImtWikw9usC1pl_9m1nQ")
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", x, re.S):
        v, t, p = (re.search(r, e) for r in (r"<yt:videoId>([^<]+)", r"<title>([^<]+)", r"<published>([^<]+)"))
        if v and t and "המלאה" in t.group(1) and ("הפטריוטים" in t.group(1) or "מהדורה" in t.group(1)):
            pub = datetime.fromisoformat(p.group(1).replace("Z", "+00:00")).astimezone(IL).strftime("%Y-%m-%d %a %H:%M") if p else ""
            out.append((v.group(1), html.unescape(t.group(1)), pub))
    return out


def rss(url, need_full):
    out = []
    for it in re.findall(r"<item>(.*?)</item>", get(url), re.S)[:10]:
        m = re.search(r"<title>(.*?)</title>", it, re.S)
        t = re.sub(r"^<!\[CDATA\[|\]\]>$", "", html.unescape(m.group(1)).strip()) if m else ""
        if t and (not need_full or "המלאה" in t):
            out.append((t, t, ""))
    return out


def gly():
    progs = []
    for ep in ("GetProgramShowOnTopMenu", "GetPodcastShowOnTopMenu"):
        progs += json.loads(get(f"https://api_new.radiodarom.co.il/api/Program/{ep}?radioStationId=2") or "[]")
    out = []
    for p in [p for p in progs if any(w in (p.get("title") or "") for w in ("פתחי", "פעמיים"))][:3]:
        page = get(f"https://www.gly.co.il/program?id={p['id']}")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
        if not m:
            continue
        for day in (json.loads(m.group(1))["props"]["pageProps"]["programData"].get("itemsByDate") or [])[:2]:
            for it in day.get("items") or []:
                if it.get("isFullProgram"):
                    out.append((str(it.get("itemId")), f"{p.get('title')}: {it.get('itemTitle') or ''}", ""))
    return out


def k13():
    def call(path, body):
        req = urllib.request.Request("https://5031.frp1.ott.kaltura.com/api_v3/service/" + path,
                                     json.dumps({"apiVersion": "5.4.0", **body}).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=40).read().decode()).get("result") or {}
    ks = call("ottuser/action/anonymousLogin", {"partnerId": 5031})["ks"]
    objs = call("asset/action/list", {"ks": ks, "filter": {"objectType": "KalturaSearchAssetFilter", "kSql": "(or SeriesName~'מהדורה' name~'מהדורה')",
                "typeIn": "1268", "orderBy": "CREATE_DATE_DESC"}, "pager": {"objectType": "KalturaFilterPager", "pageSize": 6, "pageIndex": 1}}).get("objects") or []
    return [(str(o.get("id")), o.get("name") or "", datetime.fromtimestamp(o["createDate"], IL).strftime("%Y-%m-%d %a %H:%M") if o.get("createDate") else "")
            for o in objs]


SRC = {
    "יוטיוב ערוץ 14 (הפטריוטים/מהדורות)": c14,
    "גלי ישראל (פתחי וזמרי / פעמיים הפוך)": gly,
    "103 ינון מגל ובן כספית": lambda: rss("https://103fm.maariv.co.il/rss/mediarss.aspx?programId=262", True),
    "גלצ מחשבות בעמ": lambda: rss(GLZ, False),
    "13 מהדורות": k13,
}


if __name__ == "__main__":
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {"seen": {}, "log": {}}
    while True:
        changed = False
        for name, fn in SRC.items():
            try:
                items = fn()
            except Exception as e:  # noqa: BLE001
                print("timing check failed", name, str(e)[:150], flush=True)
                continue
            old = st["seen"].get(name)
            for iid, title, pub in items:
                if old is not None and iid not in old:
                    st["log"].setdefault(name, []).append({"title": title[:90], "seen": now(), "published": pub})
                    st["log"][name] = st["log"][name][-40:]
                    changed = True
            st["seen"][name] = list(dict.fromkeys((old or []) + [i[0] for i in items]))[-80:]
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        if changed:
            try:
                lines = [f"{n} | {e['seen']} | פורסם: {e['published'] or '-'} | {e['title']}" for n, es in st["log"].items() for e in es]
                ym("UploadTextFile", what="ivr2:/AgentCode/timing.txt", contents="\n".join(lines))
            except Exception as e:  # noqa: BLE001
                print("timing upload failed", e, flush=True)
        time.sleep(300)
