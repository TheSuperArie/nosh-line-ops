"""מדידת צריכת השרת: כל 10 דקות רושם מעבד, זיכרון, דיסק ותעבורה - של השרת כולו ושל כל אחד מהסוכנים.
שומר ב-ivr2:/AgentCode/server_stats.txt (תמונת מצב עדכנית + סיכום 24 שעות). רק קורא נתונים - לא משנה כלום."""
import json, os, time, urllib.parse, urllib.request
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:  # פייתון 3.8
    from backports.zoneinfo import ZoneInfo

API = "https://www.call2all.co.il/ym/api/"
KEY = os.environ.get("YM_TOKEN", "").strip().strip("'\"").strip()
DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "stats_state.json")
IL = ZoneInfo("Asia/Jerusalem")
TICK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")


def meminfo():
    m = {}
    for l in open("/proc/meminfo"):
        k, v = l.split(":", 1)
        m[k] = int(v.split()[0]) * 1024
    return m


def net_bytes():
    rx = tx = 0
    for l in open("/proc/net/dev").readlines()[2:]:
        name, data = l.split(":", 1)
        if name.strip() == "lo":
            continue
        f = data.split()
        rx += int(f[0]); tx += int(f[8])
    return rx, tx


def cpu_total():
    f = open("/proc/stat").readline().split()[1:]
    return sum(int(x) for x in f)


def procs():
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            st = open(f"/proc/{pid}/stat").read()
            f = st[st.rfind(")") + 2:].split()
            ticks = int(f[11]) + int(f[12])
            rss = int(f[21]) * PAGE
        except (OSError, IndexError, ValueError):
            continue
        if not cmd:
            continue
        name = cmd
        for part in cmd.split():
            if part.endswith("_agent.py") or part.endswith("bootstrap.py"):
                name = "סוכן: " + os.path.basename(part)[:-3]
                break
        else:
            name = os.path.basename(cmd.split()[0])[:40]
        out[pid] = (name, ticks, rss)
    return out


def ym_upload(text):
    p = urllib.parse.urlencode({"token": KEY, "what": "ivr2:/AgentCode/server_stats.txt", "contents": text}).encode()
    urllib.request.urlopen(urllib.request.Request(API + "UploadTextFile", data=p), timeout=60).read()


def mb(x):
    return f"{x / 1024 / 1024:.0f}MB"


def main():
    try:
        hist = json.load(open(STATE))
    except (OSError, ValueError):
        hist = []
    prev_p, prev_cpu, prev_net = procs(), cpu_total(), net_bytes()
    ncpu = os.cpu_count() or 1
    wait = 90  # מדידה ראשונה מהר, אחר כך כל 10 דקות
    while True:
        time.sleep(wait)
        wait = 600
        try:
            cur_p, cur_cpu, cur_net = procs(), cpu_total(), net_bytes()
            dt = max(cur_cpu - prev_cpu, 1)  # ticks של כל הליבות יחד
            groups = {}
            for pid, (name, ticks, rss) in cur_p.items():
                d = ticks - prev_p[pid][1] if pid in prev_p else 0
                g = groups.setdefault(name, [0.0, 0, 0])
                g[0] += 100.0 * d / dt * ncpu  # אחוז מליבה אחת
                g[1] += rss
                g[2] += 1
            m = meminfo()
            sv = os.statvfs("/")
            load = open("/proc/loadavg").read().split()[:3]
            rx, tx = cur_net[0] - prev_net[0], cur_net[1] - prev_net[1]
            agents = {k: v for k, v in groups.items() if k.startswith("סוכן")}
            hist.append({"t": time.time(), "load": float(load[0]), "avail": m["MemAvailable"], "rx": max(rx, 0), "tx": max(tx, 0),
                         "agents_rss": sum(v[1] for v in agents.values()), "agents_cpu": sum(v[0] for v in agents.values())})
            hist = [h for h in hist if time.time() - h["t"] < 7 * 86400]
            json.dump(hist, open(STATE, "w"))
            day = [h for h in hist if time.time() - h["t"] < 86400]
            week = hist
            lines = [f"עודכן: {datetime.now(IL).strftime('%d/%m %H:%M')}",
                     f"שרת: {ncpu} ליבות, זיכרון {mb(m['MemTotal'])}, פנוי עכשיו {mb(m['MemAvailable'])}, "
                     f"swap בשימוש {mb(m.get('SwapTotal', 0) - m.get('SwapFree', 0))}, "
                     f"דיסק פנוי {sv.f_bavail * sv.f_frsize / 1024 ** 3:.1f}GB מתוך {sv.f_blocks * sv.f_frsize / 1024 ** 3:.1f}GB, עומס {' '.join(load)}",
                     "",
                     "== כל הסוכנים שלך יחד (10 הדקות האחרונות) ==",
                     f"מעבד {sum(v[0] for v in agents.values()):.1f}% מליבה אחת | זיכרון {mb(sum(v[1] for v in agents.values()))} | {sum(v[2] for v in agents.values())} תהליכים",
                     "", "== לפי סוכן =="]
            for k, v in sorted(agents.items(), key=lambda x: -x[1][1]):
                lines.append(f"{k}: מעבד {v[0]:.1f}% | זיכרון {mb(v[1])}")
            lines += ["", "== 10 התהליכים הכבדים בשרת (כולל של קציר) =="]
            for k, v in sorted(groups.items(), key=lambda x: -x[1][1])[:10]:
                lines.append(f"{k}: מעבד {v[0]:.1f}% | זיכרון {mb(v[1])} | {v[2]} תהליכים")
            for label, rows in (("24 שעות", day), ("מאז שהתחלתי למדוד", week)):
                if rows:
                    hrs = (rows[-1]["t"] - rows[0]["t"]) / 3600 + 1 / 6
                    lines += ["", f"== סיכום {label} ({hrs:.1f} שעות מדידה) ==",
                              f"סוכנים: מעבד ממוצע {sum(r['agents_cpu'] for r in rows) / len(rows):.1f}% מליבה, "
                              f"זיכרון ממוצע {mb(sum(r['agents_rss'] for r in rows) / len(rows))}, מקסימום {mb(max(r['agents_rss'] for r in rows))}",
                              f"שרת: עומס ממוצע {sum(r['load'] for r in rows) / len(rows):.2f}, מקסימום {max(r['load'] for r in rows):.2f}; "
                              f"זיכרון פנוי מינימלי {mb(min(r['avail'] for r in rows))}",
                              f"תעבורת השרת כולו: נכנס {sum(r['rx'] for r in rows) / 1024 ** 3:.2f}GB, יוצא {sum(r['tx'] for r in rows) / 1024 ** 3:.2f}GB"]
            ym_upload("\n".join(lines))
            prev_p, prev_cpu, prev_net = cur_p, cur_cpu, cur_net
        except Exception as e:  # noqa: BLE001
            print("stats error:", str(e)[:200], flush=True)


if __name__ == "__main__":
    main()
