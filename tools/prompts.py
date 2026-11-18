"""מקריא את תפריטי הקו (בתפריט הראשי - מעל שיר הפתיח assets/opening_song.mp3: 4 שניות שיר, ואז השיר יורד וההקראה נכנסת) (config/prompts.json) בקול "אברי" (edge-tts) ומעלה כ-wav במקום קבצי ה-tts.
ONLY=/2/M1000,/2/1/M1000 - רק חלק."""
import asyncio, json, os, subprocess, sys
import edge_tts
sys.path.insert(0, "server")
os.environ.setdefault("NEWS_STATE", "/tmp/x.json")
import news_agent as n

SONG = "assets/opening_song.mp3"
INTRO = float(os.environ.get("INTRO_SEC", "4"))   # כמה שניות השיר לבד
DUCK = float(os.environ.get("DUCK", "0.18"))      # עוצמת השיר מתחת להקראה

def dur(f):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).strip())

def with_song(voice):
    open("/tmp/v.mp3", "wb").write(voice)
    T = INTRO + dur("/tmp/v.mp3") + 1.5
    fc = (f"[0:a]aresample=48000,volume='if(lt(t,{INTRO}),1,1-{1-DUCK}*min((t-{INTRO})/0.8,1))':eval=frame[m];"
          f"[1:a]aresample=48000,aformat=channel_layouts=stereo,adelay=delays={int(INTRO*1000)}:all=1,volume=1.4[v];"
          f"[m][v]amix=inputs=2:duration=first:normalize=0,atrim=0:{T:.2f},afade=t=out:st={T-1.5:.2f}:d=1.5[o]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", SONG, "-i", "/tmp/v.mp3",
                    "-filter_complex", fc, "-map", "[o]", "-ac", "1", "-ar", "22050", "-b:a", "64k", "/tmp/mix.mp3"], check=True)
    print("CAT mix", f"{T:.1f}s")
    return open("/tmp/mix.mp3", "rb").read()

def good_news(voice):
    """הודעת "חדש" בווייב של בשורה טובה: צליל פעמונים עולה (דו-מי-סול-דו), ההודעה, ואז שקט קצר - לפני שיר הפתיח."""
    open("/tmp/gn.mp3", "wb").write(voice)
    notes = [(523.25, 0.0), (659.25, 0.13), (783.99, 0.26), (1046.5, 0.39)]
    ins, fc = [], []
    for i, (f, start) in enumerate(notes):
        ins += ["-f", "lavfi", "-i", f"sine=frequency={f}:duration=0.9"]
        fc.append(f"[{i}:a]volume=0.22,afade=t=out:st=0.05:d=0.85,adelay={int(start*1000)}:all=1,aresample=48000[n{i}]")
    k = len(notes)
    fc.append("".join(f"[n{i}]" for i in range(k)) + f"amix=inputs={k}:normalize=0,apad=pad_dur=0.25[ch]")
    fc.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,volume=1.3,apad=pad_dur=0.6[vo]")
    fc.append("[ch]aformat=channel_layouts=mono[chm];[chm][vo]concat=n=2:v=0:a=1[o]")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *ins, "-i", "/tmp/gn.mp3",
                    "-filter_complex", ";".join(fc), "-map", "[o]", "-ac", "1", "-ar", "22050", "-b:a", "64k", "/tmp/gnx.mp3"], check=True)
    return "/tmp/gnx.mp3"

def concat(first, second_bytes):
    open("/tmp/second.mp3", "wb").write(second_bytes)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", first, "-i", "/tmp/second.mp3",
                    "-filter_complex", "[0:a]aresample=22050,aformat=channel_layouts=mono[a];[1:a]aresample=22050,aformat=channel_layouts=mono[b];[a][b]concat=n=2:v=0:a=1[o]",
                    "-map", "[o]", "-ac", "1", "-ar", "22050", "-b:a", "64k", "/tmp/full.mp3"], check=True)
    print("CAT with good-news", f"{dur('/tmp/full.mp3'):.1f}s")
    return open("/tmp/full.mp3", "rb").read()

def tts(text, rate=None, pitch="+0Hz"):
    for attempt in range(3):
        try:
            asyncio.run(asyncio.wait_for(edge_tts.Communicate(text, n.VOICE, rate=rate or n.RATE, pitch=pitch).save("/tmp/p.mp3"), 60))
            return open("/tmp/p.mp3", "rb").read()
        except Exception as e:  # noqa
            print("CAT retry", e)
    return None

P = json.load(open("config/prompts.json", encoding="utf-8"))
only = [x for x in os.environ.get("ONLY", "").split(",") if x]
for path, text in P.items():
    if path.startswith("_") or "@" in path or (only and path not in only):
        continue  # "/M1000@new" = הודעת "חדש" לפני הפתיח (לא קובץ בפני עצמו)
    data = tts(text)
    if not data:
        continue
    if path == "/M1000" and os.path.exists(SONG):
        data = with_song(data)
        pre = P.get("/M1000@new")
        if pre:
            v = tts(pre, rate="+8%", pitch="+6Hz")  # טיפה יותר עליז
            if v:
                data = concat(good_news(v), data)
    n.ym("FileAction", action="delete", what=f"ivr2:{path}.tts")
    r = n.upload_audio(f"ivr2:{path}.wav", data)
    print("CAT", path, len(data), r.get("responseStatus"), r.get("message", ""))
