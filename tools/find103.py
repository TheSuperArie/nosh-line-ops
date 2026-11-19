"""אבחון: מוצא תוכנית ב-103FM לפי שם ומדפיס את הפריטים האחרונים ב-RSS (שם + תאריך)."""
import html, os, re, sys, urllib.request
UA = {"User-Agent": "Mozilla/5.0"}
get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40).read().decode("utf-8", "replace")
q = os.environ.get("Q", "שניים עד ארבע")
home = get("https://103fm.maariv.co.il/")
links = dict()
for href, txt in re.findall(r'href="([^"]*?/program/[^"]+?\.aspx)"[^>]*>(.*?)</a>', home, re.S):
    t = re.sub(r"<[^>]+>", " ", html.unescape(txt)).strip()
    if t:
        links[href] = t
cand = [(h, t) for h, t in links.items() if all(w in t for w in q.split())] or \
       [(h, t) for h, t in links.items() if any(w in t for w in q.split() if len(w) > 2)]
print("CAND", cand[:8])
for h, t in cand[:2]:
    import urllib.parse
    url = urllib.parse.quote(h if h.startswith("http") else "https://103fm.maariv.co.il" + h, safe=":/?=&.%")
    pid = re.search(r"programId=(\d+)", get(url))
    print("PROG", t, url, pid and pid.group(1))
    if pid:
        x = get(f"https://103fm.maariv.co.il/rss/mediarss.aspx?programId={pid.group(1)}")
        for it in re.findall(r"<item>(.*?)</item>", x, re.S)[:14]:
            ti = re.search(r"<title>(.*?)</title>", it, re.S)
            pd = re.search(r"<pubDate>(.*?)</pubDate>", it)
            print("ITEM", html.unescape(re.sub(r"^<!\[CDATA\[|\]\]>$", "", ti.group(1).strip())) if ti else "", "|", pd and pd.group(1))
