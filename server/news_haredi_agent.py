"""מבזקי הקו החרדי לשלוחה 8/3 (שונות) - אותו סוכן מבזקים, מסונן לערוץ אחד."""
import os, runpy
D = os.path.dirname(os.path.abspath(__file__))
os.environ.update(NEWS_EXT="/8/1/3", NEWS_AUTHORS="הקו החרדי", NEWS_SAY_CH="0", NEWS_KEEP="30",
                  NEWS_STATE=os.path.join(D, "news_haredi_state.json"))
runpy.run_path(os.path.join(D, "news_agent.py"), run_name="__main__")
