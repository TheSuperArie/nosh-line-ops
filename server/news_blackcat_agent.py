"""מבזקי החתול השחור (black-cat.thechats.click) לשלוחה 8/1/4 - אותו סוכן מבזקים, אתר באותה מערכת (TheChannel). רק טקסט."""
import os, runpy
D = os.path.dirname(os.path.abspath(__file__))
os.environ.update(NEWS_SITE="https://black-cat.thechats.click", NEWS_EXT="/8/1/4", NEWS_SAY_CH="0", NEWS_KEEP="30",
                  NEWS_STATE=os.path.join(D, "news_blackcat_state.json"))
runpy.run_path(os.path.join(D, "news_agent.py"), run_name="__main__")
