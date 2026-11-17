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

P = json.load(open("config/prompts.json", encoding="utf-8"))
only = [x for x in os.environ.get("ONLY", "").split(",") if x]
for path, text in P.items():
    if path.startswith("_") or (only and path not in only):
        continue
    data = None
    for attempt in range(3):
        try:
            asyncio.run(asyncio.wait_for(edge_tts.Communicate(text, n.VOICE, rate=n.RATE).save("/tmp/p.mp3"), 60))
            data = open("/tmp/p.mp3", "rb").read()
            break
        except Exception as e:  # noqa
            print("CAT retry", path, e)
    if not data:
        continue
    if path == "/M1000" and os.path.exists(SONG):
        data = with_song(data)
    n.ym("FileAction", action="delete", what=f"ivr2:{path}.tts")
    r = n.upload_audio(f"ivr2:{path}.wav", data)
    print("CAT", path, len(data), r.get("responseStatus"), r.get("message", ""))
