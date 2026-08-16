import json

with open("run-results.json") as f:
    results = json.load(f)
with open("attempt-result.json") as f:
    results.append(json.load(f))
with open("run-results.json", "w") as f:
    json.dump(results, f)
