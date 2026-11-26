import re, sys, json, urllib.request, urllib.parse, html
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
def get(u, n=None):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30)
        b = r.read()
        print(f"GET {r.status} {len(b)}B {u[:150]}")
        return b.decode("utf-8", "replace")
    except Exception as e:
        print("ERR", u[:150], e); return ""
# 1) iTunes search for the podcast feed
for term in ["קלמן ליברמן", "קלמן ליבסקינד אסף ליברמן", "כאן רשת ב קלמן"]:
    s = get("https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": term, "media": "podcast", "country": "IL", "limit": 8}))
    try:
        for it in json.loads(s).get("results", []):
            print("  IT", it.get("collectionName"), "|", it.get("artistName"), "|", it.get("feedUrl"))
    except Exception: pass
# 2) program page
p = get("https://www.kan.org.il/content/kan/podcasts/p-8314/")
for pat in [r'https?://[^"\'\s<>]+\.mp3[^"\'\s<>]*', r'https?://[^"\'\s<>]+\.m3u8[^"\'\s<>]*', r'https?://[^"\'\s<>]*(?:omny|rss|feed|podcast)[^"\'\s<>]*', r'/content/kan/podcasts/p-8314/\d+/?']:
    found = list(dict.fromkeys(re.findall(pat, p)))
    print("PAT", pat[:40], len(found)); [print("   ", f[:200]) for f in found[:12]]
txt = re.sub(r"<[^>]+>", " ", p); txt = re.sub(r"\s+", " ", html.unescape(txt))
print("TXT", txt[:1500])
eps = list(dict.fromkeys(re.findall(r'/content/kan/podcasts/p-8314/(\d+)/?', p)))
if eps:
    e = get(f"https://www.kan.org.il/content/kan/podcasts/p-8314/{eps[0]}/")
    for pat in [r'https?://[^"\'\s<>]+\.(?:mp3|m4a|m3u8)[^"\'\s<>]*', r'(?:data-[a-z-]*(?:src|url|audio|file)[a-z-]*)="[^"]+"', r'https?://[^"\'\s<>]*(?:omny|kaltura|streamgates|kanapi|cdn)[^"\'\s<>]*']:
        found = list(dict.fromkeys(re.findall(pat, e)))
        print("EP", pat[:40], len(found)); [print("   ", f[:220]) for f in found[:15]]
