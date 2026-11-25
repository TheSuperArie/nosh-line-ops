"""בדיקה: הניקוד האוטומטי של סוכן העלון (נקדן דיקטה) על שורות שמגיעות ב-TEXT (שורה = הגיג)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import alon_agent as a  # noqa: E402
a.log = lambda *x: print("CAT LOG", *x)
for line in (os.environ.get("TEXT") or "מלצר: ומה בשבילך? אורח: שכוי.").split("\n"):
    if line.strip():
        print("CAT", a.nikud(a.speakable(line)))
