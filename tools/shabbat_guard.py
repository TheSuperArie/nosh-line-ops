"""שומר שבת: האם מותר לסוכן לרוץ עכשיו? (שבת ויום טוב בארץ ישראל, עם מרווחים לחומרה)
מתחילת השבת/חג: שקיעה פחות 45 דקות בערב שבת/חג.  סיום: שקיעה ועוד 75 דקות במוצאי שבת/חג.
כותב ok=true/false ל-GITHUB_OUTPUT ומדפיס הסבר."""
import os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from zmanim.util.geo_location import GeoLocation
from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.hebrew_calendar.jewish_calendar import JewishCalendar

IL = ZoneInfo("Asia/Jerusalem")
LOC = GeoLocation("Jerusalem", 31.778, 35.235, "Asia/Jerusalem", elevation=754)
BEFORE, AFTER = timedelta(minutes=45), timedelta(minutes=75)

def sunset(d):
    return ZmanimCalendar(geo_location=LOC, date=d).sunset()

def assur(d):  # שבת או יום טוב (בארץ)
    return JewishCalendar(d, in_israel=True).is_assur_bemelacha()

def allowed(now):
    d = now.date()
    if assur(d) and now < sunset(d) + AFTER:
        return False, f"{d} שבת/חג - עד צאת {(sunset(d) + AFTER):%H:%M}"
    if assur(d + timedelta(days=1)) and now >= sunset(d) - BEFORE:
        return False, f"ערב שבת/חג - מ-{(sunset(d) - BEFORE):%H:%M}"
    if assur(d - timedelta(days=0)) and now >= sunset(d) + AFTER:
        return True, "מוצאי שבת/חג"
    return True, "יום חול"

now = datetime.fromisoformat(sys.argv[1]).astimezone(IL) if len(sys.argv) > 1 else datetime.now(IL)
ok, why = allowed(now)
print(f"{now:%Y-%m-%d %a %H:%M} -> {'OK' if ok else 'BLOCKED'} ({why})")
out = os.environ.get("GITHUB_OUTPUT")
if out:
    open(out, "a").write(f"ok={'true' if ok else 'false'}\n")
