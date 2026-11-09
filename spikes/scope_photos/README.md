# Photos in a small scope (DECISIONS.md, "Photos in a small scope")

Scoped photo search (`in:` a folder) measured three ways: z within the scope, against the whole library, either.

```bash
# synthetic folders from Imagenette (fast.ai, 10 labelled ImageNet classes, ~94 MB), each layout in its own LANTERN_HOME
curl -LO https://s3.amazonaws.com/fast-ai-imageclas/imagenette2-160.tgz && tar -xzf imagenette2-160.tgz
uv run python spikes/scope_photos/synthetic.py imagenette2-160/val /tmp/scope-bench 1 2 3
for s in 1 2 3; do uv run python spikes/scope_photos/index_and_eval.py /tmp/scope-bench $s; done
# the user's own index, read-only, aggregates only
uv run python spikes/scope_photos/real_index_eval.py
```

`index_and_eval.py` and `real_index_eval.py` patch `hybrid.image_search` with the variants; since the change shipped,
the "current" column is the shipped behaviour (either).
