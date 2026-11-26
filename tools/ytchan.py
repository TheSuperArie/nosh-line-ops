"""אבחון: מסרטון -> הערוץ שלו -> הסרטונים האחרונים (RSS) עם תאריכי העלאה, ואורך כל סרטון. env VID"""
import html, json, os, re, subprocess, urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "he,en;q=0.8"}


def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


vid = os.environ["VID"].strip()
o = json.loads(get(f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"))
print("VIDEO", o.get("title"), "| CHANNEL", o.get("author_name"), o.get("author_url"))
page = get(o["author_url"])
m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page) or re.search(r"channel/(UC[\w-]{22})", page)
cid = m.group(1)
print("CID", cid)
feed = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
ids = []
for e in re.findall(r"<entry>(.*?)</entry>", feed, re.S):
    v = re.search(r"<yt:videoId>(.*?)<", e).group(1)
    t = html.unescape(re.search(r"<title>(.*?)</title>", e).group(1))
    p = re.search(r"<published>(.*?)<", e).group(1)
    ids.append(v)
    print("FEED", p[:16], v, t[:100])
# אורך + תאריך לכל הסרטונים בערוץ (עד 60) דרך yt-dlp flat
for tab in ("videos", "streams", "podcasts"):
    r = subprocess.run(["yt-dlp", "--flat-playlist", "--playlist-end", "60", "--print", "%(id)s|%(duration_string)s|%(title).90s",
                        f"https://www.youtube.com/channel/{cid}/{tab}"], capture_output=True, text=True, timeout=180)
    for ln in r.stdout.splitlines():
        print("TAB", tab, ln)
    if r.returncode:
        print("TAB", tab, "ERR", r.stderr.strip().splitlines()[-1:])
r = subprocess.run(["yt-dlp", "--flat-playlist", "--print", "%(id)s|%(title).90s", f"https://www.youtube.com/channel/{cid}/playlists"],
                   capture_output=True, text=True, timeout=180)
for ln in r.stdout.splitlines():
    print("PL", ln)
