"""בדיקה: אילו טקסטים edge-tts מצליח להקריא (OK/FAIL) - לאיתור בעיית 'No audio was received'."""
import asyncio, json, re, sys
import edge_tts
P = json.load(open("config/prompts.json", encoding="utf-8"))
NIK = re.compile(r"[֑-ׇ]")
async def one(t, voice):
    try:
        await asyncio.wait_for(edge_tts.Communicate(t, voice, rate="+12%").save("/tmp/t.mp3"), 40)
        return "OK"
    except Exception as e:
        return "FAIL " + str(e)[:40]
tests = []
for k in ["/8/2/M1000", "/3/M1000", "/9/M1000"]:
    t = P[k]
    tests += [(k + " nikud", t), (k + " plain", NIK.sub("", t))]
    words = t.split()
    tests += [(k + " first-half", " ".join(words[:len(words)//2])), (k + " second-half", " ".join(words[len(words)//2:]))]
for v in ["fr-FR-RemyMultilingualNeural", "he-IL-AvriNeural"]:
    for name, t in tests:
        print("CAT", v[:10], name, asyncio.run(one(t, v)), flush=True)
