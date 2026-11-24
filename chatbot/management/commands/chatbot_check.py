"""
chatbot/management/commands/chatbot_check.py

Runs every question of your sheet through the chatbot and tells you what went wrong.
  ERROR - the engine crashed (this is what shows up as a 500 / HTML page in the API)
  FAIL  - the bot answered with an error message
  CHECK - it answered, but a word was not understood ("could not match: xyz")
  OK    - nothing suspicious
The column "Understood as" shows how the bot read your question.

  python manage.py chatbot_check Chatbot_Ques.xlsx
  python manage.py chatbot_check questions.txt --user rahul --out result.csv
"""
import csv

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from chatbot.engine import get_engine


def read_questions(path, col):
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError:
            raise CommandError("pip install openpyxl, or save the questions as a .txt file (one per line)")
        cells = [r[col] if len(r) > col else None
                 for r in openpyxl.load_workbook(path, read_only=True).active.iter_rows(values_only=True)]
    elif path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            cells = [r[col] if len(r) > col else None for r in csv.reader(fh)]
    else:
        with open(path, encoding="utf-8-sig") as fh:
            cells = [ln.rstrip("\n") for ln in fh]
    out = []
    for c in cells:
        q = str(c).strip() if c is not None else ""
        if len(q.split()) >= 2 and not q.startswith(("---", "#")):
            out.append(q)
    return out


class Command(BaseCommand):
    help = "Run a list of chatbot questions and report which ones fail and why."

    def add_arguments(self, p):
        p.add_argument("file", help=".xlsx / .csv / .txt with the questions")
        p.add_argument("--col", type=int, default=0, help="column number of the questions (0 = first)")
        p.add_argument("--user", help="username to test with (default: first superuser)")
        p.add_argument("--out", default="chatbot_check.csv")

    def handle(self, *a, **o):
        User = get_user_model()
        user = User.objects.get(username=o["user"]) if o["user"] else User.objects.filter(is_superuser=True).first()
        if not user:
            raise CommandError("no superuser found; pass --user")
        engine = get_engine()
        rows, stats = [], {"OK": 0, "CHECK": 0, "FAIL": 0, "ERROR": 0}
        for q in read_questions(o["file"], o["col"]):
            status, msg, understood = "OK", "", ""
            try:
                res = engine.answer(q, user)
                msg = (res.get("message") or "")[:200]
                understood = res.get("understood", "")
                if not res.get("ok"):
                    status = "FAIL"
                elif "could not match" in msg or "could not combine" in msg:
                    status = "CHECK"
            except Exception as e:                       # the engine only catches ValueError
                status, msg = "ERROR", f"{type(e).__name__}: {e}"
            stats[status] += 1
            rows.append([q, status, understood, msg])
            if status != "OK":
                self.stdout.write(f"[{status}] {q}\n        {understood} | {msg}")
        with open(o["out"], "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Question", "Status", "Understood as", "Bot message"])
            w.writerows(rows)
        self.stdout.write(f"\n{len(rows)} questions -> {o['out']}   {stats}")
