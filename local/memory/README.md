# Local Memory

Future M4 memory files live here. Expected subgroups:

```text
candidates.jsonl
facts.jsonl
episodes.jsonl
summaries/
```

`thought-core` and `deep-core` may propose candidates. `memory-core` is the
commit authority. Do not store raw logs, raw signals, config, or secrets here.
