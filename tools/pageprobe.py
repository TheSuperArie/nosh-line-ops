"""אבחון: טוען דפים בדפדפן ומדפיס קישורים (מסוננים לפי מילים) ובקשות וידאו/API. env URLS (;;), WORDS (|)"""
import asyncio, os, re
from playwright.async_api import async_playwright

URLS = [u for u in os.environ["URLS"].split(";;") if u.strip()]
WORDS = [w for w in (os.environ.get("WORDS") or "").split("|") if w]
MEDIA = re.compile(r"m3u8|\.mp4|\.mpd|kaltura|vimeo|jwplayer|jwpcdn|brightcove|cloudfront|akamai|redge|vod|player|/api/|graphql|wp-json", re.I)


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for url in URLS:
            print("=" * 20, url, flush=True)
            pg = await b.new_page(locale="he-IL")
            seen = set()

            async def on_resp(r):
                u = r.url
                if MEDIA.search(u) and u not in seen and not re.search(r"google|facebook|doubleclick|\.(png|jpg|webp|svg|woff2?|css)(\?|$)", u):
                    seen.add(u)
                    body = ""
                    try:
                        ct = r.headers.get("content-type") or ""
                        if "json" in ct or "mpegurl" in ct:
                            body = (await r.text())[:400].replace("\n", " ")
                    except Exception:
                        pass
                    print("REQ", r.status, u[:260], "|", body, flush=True)
            pg.on("response", on_resp)
            try:
                await pg.goto(url, wait_until="domcontentloaded", timeout=60000)
                await pg.wait_for_timeout(15000)
            except Exception as e:
                print("ERR", str(e)[:200])
            links = await pg.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, (e.innerText||e.title||'').trim().slice(0,80)])")
            n = 0
            for h, t in links:
                if not WORDS or any(w in (h + t) for w in WORDS):
                    print("A", h[:200], "|", t.replace("\n", " "))
                    n += 1
                    if n > 80:
                        break
            frames = [f.url for f in pg.frames if f.url and f.url != "about:blank"]
            print("FRAMES", frames[:10])
            await pg.close()
        await b.close()

asyncio.run(main())
