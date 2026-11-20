### Window configuration comparison

Both runs used Qwen3-VL-Embedding-2B on an RTX 3090 and Sphere’s reference scorer (Top-10; moment hits require tIoU ≥ 0.5). All 74 TSV360 videos passed size and SHA-256 checks.

| Setting | Short-window run | GitHub configuration (reproduced) |
|---|---|---|
| Window / overlap | 6 s / 2 s | 30 s / 5 s |
| Retrieval deduplication and static-window filtering | Different settings; exact values not confirmed here | Cosine threshold 0.9; static-window filtering enabled |

| Dataset | Recall@1: 6 s / 2 s | Recall@1: 30 s / 5 s |
|---|---:|---:|
| Sphere Benchmark | 52.86% | 74.75% |
| Jonghwa public video | 20.83% | 20.83% |
| TSV360 full queries + cross-video agreement | 25.75% | 27.41% |
| QVHighlights Test S50 | 18.00% | 16.00% |

The 30 s / 5 s rerun reproduced the existing table at its displayed precision. Windowing, deduplication, and static-window filtering changed together, so the effect of window length alone was not isolated.
