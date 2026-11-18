"""מבזקי הגזרה (hagizra.news) לשלוחה 8/1 (שונות) - אותו סוכן מבזקים, מאתר אחר באותה מערכת. רק טקסט."""
# v2 18/11: שמות קבועים + סינון כפילויות
import os, runpy
D = os.path.dirname(os.path.abspath(__file__))
os.environ.update(NEWS_SITE="https://hagizra.news", NEWS_EXT="/8/1/1", NEWS_SAY_CH="0", NEWS_KEEP="30",
                  NEWS_STATE=os.path.join(D, "news_gizra_state.json"))
runpy.run_path(os.path.join(D, "news_agent.py"), run_name="__main__")
