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
for k in ["/8/2/M1000", "/M1000@new", "/M1000"]:
    for kw in [{}, {"pitch": "+0Hz"}, {"pitch": "+6Hz"}, {"rate": "+8%", "pitch": "+6Hz"}]:
        async def go():
            try:
                await asyncio.wait_for(edge_tts.Communicate(P[k], "fr-FR-RemyMultilingualNeural", **({"rate": "+12%"} | kw)).save("/tmp/t.mp3"), 40)
                return "OK"
            except Exception as e:
                return "FAIL " + str(e)[:40]
        print("CAT", k, kw, asyncio.run(go()), flush=True)
