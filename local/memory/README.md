# Local Memory

`local/memory/` は、将来の M4 memory 置き場です。

想定する内容です。

```text
candidates.jsonl
facts.jsonl
episodes.jsonl
summaries/
```

Thought Core や Deep Core は記憶候補を作れます。  
確定記憶としてcommitする authority は Memory Core 側に置きます。

raw log、raw signal、config、secrets はここに置きません。
