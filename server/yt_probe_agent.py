"""בדיקה חד פעמית: האם אפשר להוריד את הפטריוטים מיוטיוב ישירות מהשרת (yt-dlp עצמאי + deno). התוצאה ב-AgentCode/yt_probe.txt."""
import io, json, os, platform, re, stat, subprocess, time, urllib.parse, urllib.request, zipfile
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
os.makedirs(D, exist_ok=True)
out = [f"machine {platform.machine()}"]
def fetch(url):
    return subprocess.run(["curl", "-sSL", "-m", "300", url], capture_output=True).stdout
def put(text):
    urllib.request.urlopen(urllib.request.Request("https://www.call2all.co.il/ym/api/UploadTextFile", data=urllib.parse.urlencode(
        {"token": KEY, "what": "ivr2:/AgentCode/yt_probe.txt", "contents": text}).encode()), timeout=60).read()
try:
    arm = "aarch64" in platform.machine() or "arm" in platform.machine()
    yd = os.path.join(D, "yt-dlp")
    if not os.path.exists(yd):
        open(yd, "wb").write(fetch("https://github.com/yt-dlp/yt-dlp/releases/latest/download/" + ("yt-dlp_linux_aarch64" if arm else "yt-dlp_linux")))
        os.chmod(yd, os.stat(yd).st_mode | stat.S_IEXEC)
    dn = os.path.join(D, "deno")
    if not os.path.exists(dn):
        z = fetch("https://github.com/denoland/deno/releases/latest/download/deno-" + ("aarch64" if arm else "x86_64") + "-unknown-linux-gnu.zip")
        zipfile.ZipFile(io.BytesIO(z)).extract("deno", D)
        os.chmod(dn, os.stat(dn).st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=D + ":" + os.environ.get("PATH", ""))
    out.append("yt-dlp " + subprocess.run([yd, "--version"], capture_output=True, text=True, env=env).stdout.strip())
    out.append("deno " + subprocess.run([dn, "--version"], capture_output=True, text=True, env=env).stdout.split("\n")[0])
    x = fetch("https://www.youtube.com/feeds/videos.xml?channel_id=UCKEImtWikw9usC1pl_9m1nQ").decode("utf-8", "replace")
    vids = re.findall(r"<yt:videoId>([^<]+)</yt:videoId>\s*<yt:channelId>[^<]*</yt:channelId>\s*<title>([^<]+)", x)
    vid = next((v for v, t in vids if "המלאה" in t), vids[0][0] if vids else "")
    out.append("video " + vid)
    for client in ("default", "tv", "web_safari", "mweb", "android_vr"):
        args = [yd, "-f", "bestaudio", "-g", "--no-warnings", f"https://www.youtube.com/watch?v={vid}"]
        if client != "default":
            args[1:1] = ["--extractor-args", f"youtube:player_client={client}"]
        r = subprocess.run(args, capture_output=True, text=True, env=env, timeout=180)
        out.append(f"{client}: rc={r.returncode} {(r.stdout.strip()[:80] or r.stderr.strip()[-250:])}")
except Exception as e:  # noqa: BLE001
    out.append("ERROR " + str(e)[:300])
put("\n".join(out))
while True:
    time.sleep(3600)
