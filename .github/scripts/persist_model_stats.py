import json
import sys

if len(sys.argv) < 2:
    print("Usage: persist_model_stats.py <run-results.json>", file=sys.stderr)
    sys.exit(1)

with open(sys.argv[1]) as f:
    new_records = json.load(f)

try:
    with open("model-stats.json") as f:
        all_records = json.load(f)
except FileNotFoundError:
    all_records = []

all_records.extend(new_records)
with open("model-stats.json", "w") as f:
    json.dump(all_records, f, indent=2)

summary = {}
for r in all_records:
    m = r["model"]
    if m not in summary:
        summary[m] = {"ok": 0, "nok": 0, "last_ok": None, "last_nok": None}
    outcome = r["outcome"]
    summary[m][outcome] += 1
    key = f"last_{outcome}"
    if summary[m][key] is None or r["date"] > summary[m][key]:
        summary[m][key] = r["date"]

with open("model-summary.json", "w") as f:
    json.dump(summary, f, indent=2)

print(f"Appended {len(new_records)} record(s). Total: {len(all_records)}")
