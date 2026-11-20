# TSV360 74-video full queries + cross-video agreement（20261116-r01）

在已完成双模型 2 FPS 矫正的 **74** 个视频上，合并两类 query：

1. 这些视频在基线 `sphere-benchmark-20261105-r02` 中的**全部原始 query**
2. `20261112-r01` 中已完成跨视频一致补标的 **163** 条 query（含新增跨视频答案）

重叠的 163 条以 agreement 版为准（保留跨视频 GT）；其余保留原始单视频 GT。

- CardSet：`sphere-cards-20261116-r01`，74 个视频
- Benchmark：`sphere-benchmark-20261116-r01`，901 条 query
  - 其中 enriched（含跨视频补标）：163
  - 仅原始 GT：738
- 一致新增的 query–视频配对：96，覆盖 69 条 query、32 个视频
- 新增 expected hit：124
- 74 视频原始 hit 总数：1188 → 合并后：1312
- 默认版本：否
- 质量状态：`unreviewed`，未经人工签核，也没有做 8 FPS 边界精修

文件：

- `benchmark.json`：可发布的 Benchmark。
- `card-set.json`、`media.json`：74 个矫正完成视频。
- `additions.json`：跨视频新增答案及视觉支持区间（与 20261112-r01 相同）。
- `verification.json`：数量和文件哈希。

SHA-256：

- `benchmark.json`：`0858e814f4a267fdb9943e7aafb0dc8de2082d316a0a277c1001f0d26e2f913b`
- `additions.json`：`c881be6741090b38305120e45664a82e24de8739849fe49ae6a733b2085e9f71`
- `card-set.json`：`53b9816f8fd83bd13b12fdd56732dd616c59ea02ccb9f53283b3420e64a65360`
- `media.json`：`7f14fcea62933a74c2e3ed9499bdbfbd3b63c0ab29dfab29015aa8baff457ce4`
- `verification.json`：`ae90e911d4a30aa9a160d4285fe4eca214acc5578830042463789a6a680eab2e`
