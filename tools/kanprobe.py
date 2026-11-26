import re, urllib.request, html
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
def get(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30); b = r.read()
        print(f"GET {r.status} {len(b)}B {r.geturl()[:200]}"); return b.decode("utf-8", "replace")
    except Exception as e:
        print("ERR", u[:150], e); return ""
for u in ["https://omny.fm/shows/kan-4240/playlists/podcast.rss", "https://omny.fm/shows/kan-4240/podcast.rss"]:
    s = get(u)
    items = re.findall(r"<item>(.*?)</item>", s, re.S)
    print("ITEMS", len(items))
    for it in items[:40]:
        g = lambda t: html.unescape((re.search(rf"<{t}[^>]*>(.*?)</{t}>", it, re.S) or [0, ""])[1]).strip()
        enc = (re.search(r'<enclosure[^>]*url="([^"]+)"[^>]*length="(\d+)"', it) or [0, "", "0"])
        print(g("pubDate")[:25], "|", g("itunes:duration"), "|", g("title")[:70], "|", int(enc[2])//1000000 if enc else "", "MB")
    if items:
        print("ENC", re.search(r'<enclosure[^>]*>', items[0]).group(0)[:300])
p = get("https://www.kan.org.il/content/kan/kan-b/p-9996/")
print("P9996 names", re.findall(r'"name":"([^"]{0,80})"', p)[:15])
print("P9996 omny", list(dict.fromkeys(re.findall(r'https://traffic\.omny\.fm/[^"\'&\s]+', p)))[:5])
print("P9996 eps", list(dict.fromkeys(re.findall(r'/content/kan/kan-b/p-9996/[^"\'\s/]+', p)))[:10])
