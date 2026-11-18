"""הורדה מאתרים אחרים (מאקו, רשת 13 ועוד): פותח את הדף בדפדפן אמיתי דרך השרת בישראל,
תופס את כתובת הזרם (m3u8/mpd) שהנגן מבקש, ומוריד אותה עם yt-dlp."""
import json
import os
import re
import subprocess

SKIP = re.compile(r"_next/static|\.js(\?|$)")


def find_streams(url: str, links_out: list | None = None, play: bool = True, texts_out: dict | None = None) -> tuple[str, list]:
    """מחזיר (כותרת הדף, רשימת זרמים שנמצאו עם הכותרות שהנגן שלח).
    links_out - אם נשלחה רשימה, ממלאים בה את כל הקישורים שבדף (לזיהוי דף רשימת פרקים)."""
    from playwright.sync_api import sync_playwright
    found, title = [], ""
    proxy = os.environ.get("IL_PROXY")
    stealth = False
    try:  # Patchright = Playwright עם תיקוני הסוואה; עם Chrome האמיתי קשה יותר לחומות בוטים לזהות
        from patchright.sync_api import sync_playwright as stealth_pw
        sync_playwright, stealth = stealth_pw, True  # noqa: F811
    except ImportError:
        pass
    with sync_playwright() as p:
        b = None
        if stealth:
            import tempfile
            ctx = p.chromium.launch_persistent_context(
                tempfile.mkdtemp(), channel="chrome", headless=False, no_viewport=True,
                locale="he-IL", timezone_id="Asia/Jerusalem",
                proxy={"server": proxy} if proxy else None,
                args=["--autoplay-policy=no-user-gesture-required"])
            print("::notice title=SITE browser::patchright + chrome", flush=True)
        else:
            b = p.chromium.launch(headless=os.environ.get("HEADFUL") != "1",
                                  proxy={"server": proxy} if proxy else None,
                                  args=["--autoplay-policy=no-user-gesture-required",
                                        "--disable-blink-features=AutomationControlled"])
            ctx = b.new_context(locale="he-IL", timezone_id="Asia/Jerusalem", viewport={"width": 1366, "height": 768},
                                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                                extra_http_headers={"Accept-Language": "he-IL,he;q=0.9,en;q=0.8"})
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.pages[0] if stealth and ctx.pages else ctx.new_page()

        def on_resp(r):
            u = r.url
            if re.search(r"\.(m3u8|mpd)(\?|$)", u) and not SKIP.search(u) and r.status == 200:
                if all(f["url"] != u for f in found):
                    hd = {k: v for k, v in r.request.headers.items()
                          if k.lower() in ("referer", "origin", "user-agent")}
                    found.append({"url": u, "headers": hd})
        page.on("response", on_resp)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        # חומת הבוטים של מאקו (Radware): לפעמים עוברת אחרי כניסה לדף הבית, המתנה וניסיון חוזר
        for attempt in range(3):
            t = page.title()
            if not re.search(r"radware|block|captcha|access denied", t, re.I):
                break
            print(f"::warning title=SITE blocked::attempt {attempt + 1}: {t}", flush=True)
            home = re.match(r"https?://[^/]+/", url)
            if home and attempt == 0:
                page.goto(home.group(0), wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(6000)
                page.mouse.move(400, 300)
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(4000)
            else:
                page.wait_for_timeout(15000)
            page.goto(url, wait_until="domcontentloaded", timeout=60000, referer=home.group(0) if home else None)
            page.wait_for_timeout(8000)
        if links_out is not None:  # גלילה כדי שכל הרשימה תיטען, ואיסוף הקישורים
            for _ in range(8):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(700)
            try:
                pairs = page.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, (e.innerText || e.title || e.getAttribute('aria-label') || (e.querySelector('img') || {}).alt || '').trim().replace(/\\s+/g, ' ').slice(0, 120)])")
                links_out.extend(p[0] for p in pairs)
                if texts_out is not None:
                    for h, t in pairs:
                        if t and len(t) > len(texts_out.get(h, "")):
                            texts_out[h] = t
            except Exception:  # noqa: BLE001
                pass
        if play:
            for sel in ["video", "button[aria-label*='play' i]", ".vjs-big-play-button", "[class*='play']"]:
                try:
                    page.locator(sel).first.click(timeout=2000)
                    break
                except Exception:  # noqa: BLE001
                    pass
            page.wait_for_timeout(20000)  # מספיק זמן גם לפרסומת שלפני הפרק
        title = page.title()
        (b or ctx).close()
    return title, found


def ytdlp_base(stream: dict) -> list:
    cmd = ["yt-dlp", "--no-warnings"]
    if os.environ.get("IL_PROXY"):
        cmd += ["--proxy", os.environ["IL_PROXY"]]
    for k, v in stream["headers"].items():
        cmd += ["--add-headers", f"{k}:{v}"]
    return cmd


def probe(stream: dict) -> dict | None:
    """מה יש בזרם: גובה מקסימלי, משך, שידור חי, הצפנה."""
    r = subprocess.run(ytdlp_base(stream) + ["-J", stream["url"]], capture_output=True, text=True, timeout=120)
    try:
        info = json.loads(r.stdout)
    except ValueError:
        return None
    fm = info.get("formats") or []
    heights = [f.get("height") or 0 for f in fm]
    return {"height": max(heights or [0]), "duration": info.get("duration") or 0,
            "live": bool(info.get("is_live")) or info.get("live_status") == "is_live",
            "drm": any(f.get("has_drm") for f in fm)}


def pick_best(found: list) -> tuple[dict | None, str]:
    """בוחר את הזרם של הפרק עצמו: לא שידור חי, לא מוצפן, הכי ארוך ובאיכות הכי גבוהה."""
    cands, saw_live, saw_drm = [], False, False
    for s in found[:8]:
        info = probe(s)
        print(f"::notice title=SITE probe::{info} {s['url'][:160]}", flush=True)
        if not info or not info["height"]:
            continue
        if info["live"]:
            saw_live = True
            continue
        if info["drm"]:
            saw_drm = True
            continue
        cands.append((info["duration"] // 60, info["height"], s, info))
    if not cands:
        return None, ("live" if saw_live else "drm" if saw_drm else "none")
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)  # קודם הארוך (הפרק ולא פרסומת), אחר כך האיכות
    best = cands[0]
    best[2]["info"] = best[3]
    return best[2], ""


def download(stream: dict, out_path: str, max_h: int = 1080) -> None:
    cmd = ytdlp_base(stream) + ["-f", "bv*+ba/b", "-S", f"res:{max_h},vcodec:h264,acodec:aac",
                                "--merge-output-format", "mp4", "--concurrent-fragments", "8",
                                "-o", out_path, stream["url"]]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-1500:])


def episode_links(list_url: str, links: list) -> tuple[list, list]:
    """מתוך הקישורים שבדף רשימה: (קישורים לפרקים, קישורים לעמודים נוספים של הרשימה).
    פרק = קישור באותו אתר שהנתיב שלו מכיל את הנתיב של דף הרשימה ועוד משהו (למשל /item/<רשימה>/<פרק>/)."""
    from urllib.parse import urlparse
    base = urlparse(list_url)
    lpath = base.path.rstrip("/")
    lpath = re.sub(r"/page/\d+$", "", lpath)
    eps, pages, seen = [], [], set()
    for h in links:
        h = h.split("#")[0]
        u = urlparse(h)
        if u.netloc != base.netloc:
            continue
        path = u.path.rstrip("/")
        clean = f"{u.scheme}://{u.netloc}{u.path}"
        if re.fullmatch(re.escape(lpath) + r"/page/\d+", path):
            if clean not in pages:
                pages.append(clean)
            continue
        if lpath and lpath in path and len(path) > len(path.split(lpath)[0]) + len(lpath) + 1 \
                and not re.search(r"/(shorts|[\w-]*clips|page|category|tag)/", path[path.find(lpath) + len(lpath):]):
            if path not in seen:
                seen.add(path)
                eps.append(h)
    if len(eps) < 2:  # הפרקים בכתובת אחרת (למשל דף /shows/... שמקשר ל-/allshows/series/<id>/season/<n>/<פרק>/)
        groups: dict = {}
        for h in links:
            h = h.split("#")[0]
            u = urlparse(h)
            if u.netloc != base.netloc or not EPISODE_RE.search(u.path) or re.search(r"/(shorts|[\w-]*clips)/", u.path):
                continue
            parent = u.path.rstrip("/").rsplit("/", 1)[0]
            g = groups.setdefault(parent, [])
            if all(urlparse(x).path.rstrip("/") != u.path.rstrip("/") for x in g):
                g.append(h)
        if lpath in groups:  # דף הרשימה הוא בעצמו ה"תיקייה" של הפרקים
            groups = {lpath: groups[lpath]}
        if groups:
            best = max(groups.values(), key=len)  # הקבוצה הגדולה = הפרקים של התוכנית בדף; השאר המלצות
            if len(best) >= 2:
                eps = best
    return eps, pages


EPISODE_RE = re.compile(r"(/allshows/series/\d+/season/\d+/\d+/?$|/item/.+/episodes/[^/]+/?$|/VOD-\w+\.htm$|/Video-\w+\.htm$)")
