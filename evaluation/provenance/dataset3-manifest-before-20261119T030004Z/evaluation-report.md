# SentrySearch 四数据集精度评估

更新时间（UTC）：2026-11-18T16:06:20.605204+00:00

任务状态：**completed**。

环境：Python 3.12.16 (main, Oct 26 2026, 14:10:56) [MSC v.1944 64 bit (AMD64)]; GPU: NVIDIA GeForce RTX 3090.
后端 / 模型：`local` / `qwen2b`。

| 数据集 | 状态 | 视频 | 查询 | R@1 | R@10 | nDCG@10 | mean tIoU | 无结果误报率 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| dataset1 | completed | 47 | 300 | 74.75% | 81.91% | 77.38% | 0.6793 | 100.00% |
| dataset2 | completed | 12 | 32 | 20.83% | 18.75% | 18.66% | 0.1126 | 100.00% |
| dataset3 | skipped_alignment | 74 | 901 | — | — | — | — | — |
| dataset4 | completed | 50 | 50 | 16.00% | 17.80% | 16.56% | 0.1156 | — |

## 评估口径

直接调用 sphere-eval 的 `sphere_eval/eval/metrics.py`，不使用模拟检索。Top-10；moment 要求资产一致且 tIoU ≥ 0.5，asset 只要求资产一致。返回窗口与 GT 一对一匹配。R@K 分母为 min(K, GT 数)，mean tIoU 对全部 GT 求均值，未命中计零；no_result 不进入排名指标。各查询等权。
视频按项目默认 30 秒切片、5 秒重叠，预处理为 480p / 5 FPS；本地模型再按项目规则采样（1 FPS，最多 32 帧）。静止跳过：True；检索去重相似度：0.9。不使用重排。
CLI 在 `--no-trim` 模式仍返回低置信结果，因此主表保留这些返回值。无结果误报率按任何返回命中计误报；另列默认相似度阈值下的误报率供参考，未用 GT 调参。
严格校验视频 SHA-256、大小及 GT 时间范围；CardSet 时长仅记录，不用作容器时长相等性断言。改名视频可通过内容哈希对齐。视频或标注不一致则整套跳过，失败不计零分。Card/GT 文本不会送入模型。
每个数据集在独立子进程中执行。开始前删除本评估工作区 active 内全部旧视频、Chroma 索引及临时切片；结束后再次清理。JSON 逐查询证据和 Markdown 报告保留用于审核。
这些发布包均标注 unreviewed，分数对应提供的候选 GT。固定 30 秒窗口对很短或很长的 GT 有定位上限；报告不将检索和精确边界定位混为一项。

环境验证：真实 CUDA 运算、FFmpeg 编解码、文字和视频推理及 Chroma 写入/查询通过；
自动测试 484 项，失败 0，错误 0。详见 `environment.json`、`validation.json`。

## dataset1 — sphere-benchmark-20261028-r02

状态：completed。

哈希对齐：47/47；不一致 0；多余视频 0。

索引切片 62；静止跳过 0；用时 370.22 秒。
Case：`{'positive': 297, 'no_result': 3}`；Grain：`{'moment': 175, 'asset': 125}`。
相似度 ≥ 0.41 的 no_result 误报率：100.00%。

| 粒度 | 查询 | R@1 | R@10 | nDCG@10 | mean tIoU |
|---|---:|---:|---:|---:|---:|
| Asset | 125 | 91.13% | 97.66% | 92.75% | None |
| Moment | 175 | 63.01% | 70.62% | 66.37% | 0.6793 |

逐查询结果及对齐证据：[results/dataset1.json](results/dataset1.json)

## dataset2 — sphere-public-video-jonghwa-12v-32q-20261104-r01

状态：completed。

哈希对齐：12/12；不一致 0；多余视频 0。

索引切片 75；静止跳过 0；用时 703.77 秒。
Case：`{'positive': 20, 'no_result': 8, 'near_miss': 4}`；Grain：`{'moment': 32}`。
相似度 ≥ 0.41 的 no_result 误报率：75.00%。

| 粒度 | 查询 | R@1 | R@10 | nDCG@10 | mean tIoU |
|---|---:|---:|---:|---:|---:|
| Asset | 0 | — | — | — | None |
| Moment | 32 | 20.83% | 18.75% | 18.66% | 0.1126 |

逐查询结果及对齐证据：[results/dataset2.json](results/dataset2.json)

## dataset3 — sphere-public-tsv360-cross-video-agreement-full-queries-20261116-r01

状态：skipped_alignment。

原因：提供的 ZIP 与 CardSet 媒体清单不能完全按 SHA-256 和大小一对一对齐，详见下方和 JSON。

哈希对齐：72/74；不一致 2；多余视频 2。
- `tsv360-sha256-b068562826dca9e6e7fce4564eac8e95cf3ba971aba547d998fb6465373961f9`：期望 SHA-256 `b068562826dca9e6e7fce4564eac8e95cf3ba971aba547d998fb6465373961f9`；候选：`[{"member": "videos_fix_format_74_cross_video_agreement/102_LL360.mp4", "size": 50590296, "sha256": "f9461c1c421a8e0149cdd08c99cc88d5fa631af58d9d76b5e7216d15bd096623"}]`。
- `tsv360-sha256-6fe647a223bc552df4f051f365503049c7f9d83213b86337dfe9e6d5cba9c2b2`：期望 SHA-256 `6fe647a223bc552df4f051f365503049c7f9d83213b86337dfe9e6d5cba9c2b2`；候选：`[{"member": "videos_fix_format_74_cross_video_agreement/170_LL360.mp4", "size": 52508903, "sha256": "ef9086bb9ec674f56bd3809aa22765cfdb18a43856241cb0930bf46805db9981"}]`。

逐查询结果及对齐证据：[results/dataset3.json](results/dataset3.json)

## dataset4 — sphere-public-qvhighlights-test-s50-20261113-r01

状态：completed。

哈希对齐：50/50；不一致 0；多余视频 0。

索引切片 299；静止跳过 0；用时 2210.34 秒。
Case：`{'positive': 50}`；Grain：`{'moment': 50}`。
相似度 ≥ 0.41 的 no_result 误报率：—。

| 粒度 | 查询 | R@1 | R@10 | nDCG@10 | mean tIoU |
|---|---:|---:|---:|---:|---:|
| Asset | 0 | — | — | — | None |
| Moment | 50 | 16.00% | 17.80% | 16.56% | 0.1156 |

逐查询结果及对齐证据：[results/dataset4.json](results/dataset4.json)

## 可复现与完成检查

运行指纹：`587511e91a02407a45f6ff54ced7b5ef13a1e75fec53df1e2e4abca8f880a9bc`。
输入 JSON、项目源码、uv.lock、评分源码及 ZIP 文件身份记录在 completion.json。下次执行先检查完成标记、环境、报告和全部结果；全部通过则直接退出。源码、配置、数据或资源发生变化时重新测试。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_sphere.py
```
