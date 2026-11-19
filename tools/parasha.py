"""שם הפרשה (מנוקד) לדרשה: קודם לפי שם הסרטון, ואם אין - הפרשה של השבת הקרובה אחרי ההעלאה (hebcal, ישראל)."""
import json, re, urllib.request
from datetime import datetime, timedelta, timezone

# (שם ב-hebcal, מנוקד, כתיבים אפשריים בשם הסרטון)
P = [
    ("Bereshit", "בְּרֵאשִׁית", ["בראשית"]), ("Noach", "נֹחַ", ["נח"]), ("Lech-Lecha", "לֶךְ לְךָ", ["לך לך"]),
    ("Vayera", "וַיֵּרָא", ["וירא"]), ("Chayei Sara", "חַיֵּי שָׂרָה", ["חיי שרה"]), ("Toldot", "תּוֹלְדֹת", ["תולדות", "תולדת"]),
    ("Vayetzei", "וַיֵּצֵא", ["ויצא"]), ("Vayishlach", "וַיִּשְׁלַח", ["וישלח"]), ("Vayeshev", "וַיֵּשֶׁב", ["וישב"]),
    ("Miketz", "מִקֵּץ", ["מקץ"]), ("Vayigash", "וַיִּגַּשׁ", ["ויגש"]), ("Vayechi", "וַיְחִי", ["ויחי"]),
    ("Shemot", "שְׁמוֹת", ["שמות"]), ("Vaera", "וָאֵרָא", ["וארא"]), ("Bo", "בֹּא", ["בא"]), ("Beshalach", "בְּשַׁלַּח", ["בשלח"]),
    ("Yitro", "יִתְרוֹ", ["יתרו"]), ("Mishpatim", "מִשְׁפָּטִים", ["משפטים"]), ("Terumah", "תְּרוּמָה", ["תרומה"]),
    ("Tetzaveh", "תְּצַוֶּה", ["תצוה", "תצווה"]), ("Ki Tisa", "כִּי תִשָּׂא", ["כי תשא", "כי תישא"]),
    ("Vayakhel", "וַיַּקְהֵל", ["ויקהל"]), ("Pekudei", "פְּקוּדֵי", ["פקודי"]),
    ("Vayikra", "וַיִּקְרָא", ["ויקרא"]), ("Tzav", "צַו", ["צו"]), ("Shmini", "שְׁמִינִי", ["שמיני"]),
    ("Tazria", "תַזְרִיעַ", ["תזריע"]), ("Metzora", "מְצֹרָע", ["מצורע", "מצרע"]), ("Achrei Mot", "אַחֲרֵי מוֹת", ["אחרי מות"]),
    ("Kedoshim", "קְדֹשִׁים", ["קדושים", "קדשים"]), ("Emor", "אֱמֹר", ["אמור", "אמר"]), ("Behar", "בְּהַר", ["בהר"]),
    ("Bechukotai", "בְּחֻקֹּתַי", ["בחוקותי", "בחקותי", "בחקתי"]),
    ("Bamidbar", "בְּמִדְבַּר", ["במדבר"]), ("Nasso", "נָשֹׂא", ["נשא"]), ("Beha'alotcha", "בְּהַעֲלֹתְךָ", ["בהעלותך", "בהעלתך"]),
    ("Sh'lach", "שְׁלַח לְךָ", ["שלח לך", "שלח"]), ("Korach", "קֹרַח", ["קרח", "קורח"]), ("Chukat", "חֻקַּת", ["חקת", "חוקת"]),
    ("Balak", "בָּלָק", ["בלק"]), ("Pinchas", "פִּינְחָס", ["פנחס", "פינחס"]), ("Matot", "מַטּוֹת", ["מטות"]), ("Masei", "מַסְעֵי", ["מסעי"]),
    ("Devarim", "דְּבָרִים", ["דברים"]), ("Vaetchanan", "וָאֶתְחַנַּן", ["ואתחנן"]), ("Eikev", "עֵקֶב", ["עקב"]),
    ("Re'eh", "רְאֵה", ["ראה"]), ("Shoftim", "שֹׁפְטִים", ["שופטים"]), ("Ki Teitzei", "כִּי תֵצֵא", ["כי תצא", "כי תיצא"]),
    ("Ki Tavo", "כִּי תָבוֹא", ["כי תבוא", "כי תבא"]), ("Nitzavim", "נִצָּבִים", ["נצבים", "ניצבים"]),
    ("Vayeilech", "וַיֵּלֶךְ", ["וילך"]), ("Ha'azinu", "הַאֲזִינוּ", ["האזינו"]), ("Vezot Haberakhah", "וְזֹאת הַבְּרָכָה", ["וזאת הברכה"]),
]
BY_EN = {e: n for e, n, _ in P}


def from_title(title):
    t = re.sub(r"[֑-ׇ]", "", title or "")
    t = " " + re.sub(r"[^א-ת]+", " ", t.replace("״", "").replace('"', "").replace("׳", "")) + " "
    hits = []
    for _, nik, alts in P:
        for a in sorted(alts, key=len, reverse=True):
            i = t.find(" " + a + " ")
            if i >= 0:
                hits.append((i, len(a), nik))
                break
    hits.sort()
    out = []
    for i, n, nik in hits:  # בלי חפיפות (שלח בתוך "שלח לך" וכו')
        if not out or i >= out[-1][0] + out[-1][1]:
            out.append((i, n, nik))
    return " ".join(h[2] for h in out[:2])


def from_date(ts):
    d = datetime.fromtimestamp(int(ts), timezone(timedelta(hours=2))).date()
    url = (f"https://www.hebcal.com/hebcal?v=1&cfg=json&s=on&i=on&start={d}&end={d + timedelta(days=21)}")
    items = json.loads(urllib.request.urlopen(url, timeout=30).read().decode()).get("items") or []
    for it in items:
        if it.get("category") == "parashat":
            en = it["title"].replace("Parashat ", "")
            if en in BY_EN:
                return BY_EN[en]
            return " ".join(BY_EN.get(p, "") for p in en.split("-")).strip()
    return ""


def name(title, ts=None):
    p = from_title(title)
    if not p and ts:
        try:
            p = from_date(ts)
        except Exception:  # noqa: BLE001
            p = ""
    return ("פָּרָשַׁת " + p) if p else ""


if __name__ == "__main__":
    for t in ["הרב ברוך רוזנבלום - פרשת ויצא תשפז", "הרב רוזנבלום ברוך מקץ חנוכה - תשפד", "הרב רוזנבלום ברוך וישב תשפ״ו",
              "הרב רוזנבלום ברוך פרשת כי תשא - פורים - תשפה", "הרב ברוך רוזנבלום ויקהל פקודי", "הרב ברוך רוזנבלום - פרשת שלח",
              "Rabbi Rosenblum, long live Sarah 5786"]:
        print(t, "->", name(t))
