"""Simulated launcher sessions on the user's real apps, with Lantern's real name matching (no model needed)."""
import json, random, sqlite3, sys
from lantern.search.apps import name_match, _plain

random.seed(7)
conn = sqlite3.connect(sys.argv[1]); conn.row_factory = sqlite3.Row
apps = [(r["name"], json.loads(r["names"]), json.loads(r["keywords"])) for r in conn.execute("SELECT name, names, keywords FROM apps")]
names = [a[0] for a in apps]
popular = ["Google Chrome", "Discord", "Spotify", "Cursor", "Ghostty", "Finder", "Muzyka", "Notatki", "Ustawienia systemowe",
           "Claude", "PyCharm", "Docker", "Kalendarz", "Poczta", "Safari", "Obsidian", "IntelliJ IDEA", "DBeaver", "CLion",
           "RStudio", "Counter-Strike 2", "Kalkulator", "Podgląd", "Monitor aktywności", "App Store", "Wiadomości", "Zdjęcia",
           "FaceTime", "Przypomnienia", "Terminal", "Mapy", "Pogoda", "Pages", "Numbers", "Keynote"]
order = [n for n in popular if n in names] + [n for n in names if n not in popular]
weights = [1 / (i + 1) ** 1.1 for i in range(len(order))]  # Zipf
habit = {n: random.choices([1, 2, 3, 4], [0.12, 0.5, 0.28, 0.1])[0] for n in order}  # the abbreviation one uses

def typed(app):
    base = _plain(app)
    r = random.random()
    if r < 0.05 and len(base) > 3:  # a typo in a 3-letter prefix
        p = list(base[:3]); i = random.randrange(2); p[i], p[i + 1] = p[i + 1], p[i]; return "".join(p)
    if r < 0.09 and " " in base:  # initials
        return "".join(w[0] for w in base.split())
    n = habit[app] if r < 0.85 else random.choice([1, 2, 3, 4])
    return base[:n]

sessions = []
for t in range(4000):  # ~90 days, ~45 launches a day
    app = random.choices(order, weights)[0]
    q = typed(app)
    cands = []
    for name, others, keywords in apps:
        m = name_match(q, [name, *others], keywords)
        if m:
            cands.append({"app": name, "level": m[0], "score": m[1]})
    cands.sort(key=lambda c: (-c["score"], len(c["app"]), c["app"]))
    sessions.append({"t": t, "query": q, "target": app, "cands": cands[:12]})
json.dump({"sessions": sessions, "apps": order}, open(sys.argv[2], "w"))
found = sum(any(c["app"] == s["target"] for c in s["cands"]) for s in sessions)
print(len(sessions), "sessions;", found, "with the target among Lantern's candidates;", len(order), "apps")
