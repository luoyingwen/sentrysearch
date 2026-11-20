# Sphere 精度评估

后端：local；模型：qwen2b；768 维嵌入；6 秒片段、2 秒重叠；全库检索；禁用静态片段跳过，无重排或检索去重。

## Ranking and temporal overlap

直接复用 sphere-eval/sphere_eval/eval/metrics.py：K=10，moment 命中要求同资产且 tIoU ≥ 0.5，asset 命中按同资产；候选按 tIoU 降序做一对一匹配。Recall@K 分母为 min(K, 标注数)；nDCG 使用二元命中；mean tIoU 仅统计 moment 查询，未命中标注计 0。no_result 查询不计入排名指标，另报假阳性率。

每个数据集在独立临时目录运行，退出后删除视频、索引及失败队列，再开始下一个数据集。保留报告、日志与预测结果用于审计。TSV360 的 xxx.mp4 映射为 xxx_LL360.mp4，并继续严格验证文件大小和 SHA-256。

复现：`uv run --extra local python scripts/evaluate_sphere.py`

| 数据集 | 视频 | 查询 | 排名查询 | Recall@1 | Recall@10 | nDCG@10 | mean tIoU |
|---|---:|---:|---:|---:|---:|---:|---:|
| sphere-benchmark-20261028-r02 | 47 | 300 | 297 | 52.86% | 58.95% | 52.82% | 0.2558 |
| sphere-public-video-jonghwa-12v-32q-20261104-r01 | 12 | 32 | 24 | 20.83% | 43.06% | 27.22% | 0.2817 |
| sphere-public-tsv360-cross-video-agreement-full-queries-20261116-r01 | 74 | 901 | 901 | 25.75% | 41.10% | 31.31% | 0.1514 |
| sphere-public-qvhighlights-test-s50-20261113-r01 | 50 | 50 | 50 | 18.00% | 24.02% | 20.43% | 0.1559 |

## sphere-benchmark-20261028-r02
- SHA-256 对齐：47 个视频；300 条查询。
- 无答案查询：3；假阳性数：3；假阳性率：100.00%。

## sphere-public-video-jonghwa-12v-32q-20261104-r01
- SHA-256 对齐：12 个视频；32 条查询。
- 无答案查询：8；假阳性数：8；假阳性率：100.00%。

## sphere-public-tsv360-cross-video-agreement-full-queries-20261116-r01
- SHA-256 对齐：74 个视频；901 条查询。
- 无答案查询：0；假阳性数：0；假阳性率：N/A。

## sphere-public-qvhighlights-test-s50-20261113-r01
- SHA-256 对齐：50 个视频；50 条查询。
- 无答案查询：0；假阳性数：0；假阳性率：N/A。
