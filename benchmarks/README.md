# Obsidian benchmark

`obsidian_gold.json` is a small, human-reviewed gold set used to measure the
MVP's project classification and tag suggestions. Keep the schema stable and
record precision, recall, new-tag rate, and project accuracy for each run.

Run locally with:

```bash
python -m benchmarks.run_local ../mock_vault --output benchmarks/report.json
```

The MVP keeps semantic context bounded to excerpts and local candidates. Project
confidence below 0.7 is verified or escalated; unresolved low confidence requires
user input. Raspberry Pi timing and memory validation remain deferred.
