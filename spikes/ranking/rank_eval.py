import json, sys
from collections import Counter, defaultdict
import numpy as np, lightgbm as lgb

data = json.load(open(sys.argv[1])); sessions = data["sessions"]
LEVELS = ["exact", "prefix", "word", "initials", "typo", "keyword", "leading", "contains"]
split = int(len(sessions) * 0.7)

def learned(hist_q, q):  # the Alfred-like rule shipped in Lantern
    c = hist_q[q]; total = sum(c.values())
    if not total: return None
    app, n = c.most_common(1)[0]
    return app if n >= 2 and n / total >= 0.6 else None

def features(s, cand, hist_q, hist_app, last_seen, first_letter):
    q = s["query"]; a = cand["app"]
    qc = hist_q[q]; tot = sum(qc.values())
    return [cand["score"], *[cand["level"] == l for l in LEVELS], len(a), len(q),
            hist_app[a], qc[a], qc[a] / tot if tot else 0.0, first_letter[q[:1]][a],
            s["t"] - last_seen.get(a, -10_000)]

def run():
    hist_q, hist_app, last_seen = defaultdict(Counter), Counter(), {}
    first_letter = defaultdict(Counter)
    rows = []  # (session index, features list per cand, labels, apps, rule pick)
    for i, s in enumerate(sessions):
        feats = [features(s, c, hist_q, hist_app, last_seen, first_letter) for c in s["cands"]]
        labels = [int(c["app"] == s["target"]) for c in s["cands"]]
        rows.append((i, feats, labels, [c["app"] for c in s["cands"]], learned(hist_q, s["query"])))
        # the user opens the target (if Lantern showed it; otherwise they retype: still counts as a choice for q)
        hist_q[s["query"]][s["target"]] += 1; hist_app[s["target"]] += 1; last_seen[s["target"]] = s["t"]
        first_letter[s["query"][:1]][s["target"]] += 1
    return rows

rows = run()
train = [r for r in rows[:split] if any(r[2])]
X = np.array([f for r in train for f in r[1]], dtype=float); y = np.array([l for r in train for l in r[2]])
groups = [len(r[1]) for r in train]
model = lgb.train({"objective": "lambdarank", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 10,
                   "verbose": -1}, lgb.Dataset(X, y, group=groups), num_boost_round=200)

test = rows[split:]
def top1(pick_fn):
    hit = sum(1 for r in test if pick_fn(r) == sessions[r[0]]["target"])
    return hit / len(test)
def base(r): return r[3][0] if r[3] else None
def rule(r): return r[4] or base(r)
def rule_any(r): return r[4] or base(r)  # rule may also surface an app outside the candidates
def rule_in(r): return (r[4] if r[4] in r[3] else None) or base(r)
def tree(r):
    if not r[3]: return None
    return r[3][int(np.argmax(model.predict(np.array(r[1], dtype=float))))]
def both(r): return (r[4] if r[4] in r[3] else None) or tree(r)
def both_any(r): return r[4] or tree(r)
reachable = sum(1 for r in test if any(r[2])) / len(test)
print(f"test sessions: {len(test)}; target among candidates: {reachable:.1%}")
for name, fn in [("A Lantern (names only)", base), ("B Alfred rule (row must be shown)", rule_in),
                 ("B' Alfred rule (may add the app)", rule_any), ("C tree (LambdaRank)", tree),
                 ("D rule + tree", both), ("D' rule (may add) + tree", both_any)]:
    print(f"  {name:36} top-1 {top1(fn):.1%}")
imp = sorted(zip(model.feature_importance("gain"), ["score", *LEVELS, "name_len", "query_len", "app_opens",
             "q_opens", "q_share", "first_letter_opens", "recency"]), reverse=True)[:6]
print("  tree's most useful features:", ", ".join(f"{n} ({g:.0f})" for g, n in imp))
