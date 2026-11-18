"""מבזקי הפרגוד לשלוחה 8/2 (שונות) - אותו סוכן מבזקים, מסונן לערוץ אחד."""
# v2 18/11: שמות קבועים + סינון כפילויות
import os, runpy
D = os.path.dirname(os.path.abspath(__file__))
os.environ.update(NEWS_EXT="/8/1/2", NEWS_AUTHORS="הפרגוד", NEWS_SAY_CH="0", NEWS_KEEP="30",
                  NEWS_STATE=os.path.join(D, "news_pargod_state.json"))
runpy.run_path(os.path.join(D, "news_agent.py"), run_name="__main__")
