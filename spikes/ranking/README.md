# Learned choices vs a tree model (2026-10-17)

Simulated launcher use on the apps of a real index, ranked by Lantern's real name matching, to see whether a
learning-to-rank tree adds anything to the Alfred-like rule (`hybrid.learned_choice`).

```bash
uv run python spikes/ranking/sim_sessions.py ~/Library/Application\ Support/Lantern/index.db /tmp/sessions.json
# LightGBM in a throwaway environment (not a project dependency):
uv venv /tmp/mlvenv --python 3.12 && uv pip install --python /tmp/mlvenv/bin/python lightgbm numpy
/tmp/mlvenv/bin/python spikes/ranking/rank_eval.py /tmp/sessions.json
```

Sessions: 4000 over ~90 days, app popularity Zipf-distributed (common apps first), each app with a habitual
abbreviation (85%), sometimes another length, 5% typos, 4% initials. Train on the first 70%, test on the rest.
Results are in docs/DECISIONS.md ("Learned choices vs a tree model").
