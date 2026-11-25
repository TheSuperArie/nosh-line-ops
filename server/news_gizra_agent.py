"""מבזקי הגזרה (hagizra.news) לשלוחה 1 (הראשית) - אותו סוכן מבזקים, מאתר אחר באותה מערכת. טקסט + קבצי תוכן (סרטונים/הקלטות).
25/11: הוחלף עם אותנטי ניוז - הגזרה בשלוחה 1, אותנטי ב-8/1/1."""
# v3 25/11: שלוחה 1 + קבצי תוכן
import os, runpy
D = os.path.dirname(os.path.abspath(__file__))
os.environ.update(NEWS_SITE="https://hagizra.news", NEWS_EXT="/1", NEWS_SAY_CH="0", NEWS_KEEP="250", NEWS_MEDIA="1",
                  NEWS_STATE=os.path.join(D, "news_gizra1_state.json"))
runpy.run_path(os.path.join(D, "news_agent.py"), run_name="__main__")
