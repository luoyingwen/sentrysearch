# SentrySearch Confluence 第 1–4 部分准确性审查

审查日期：2026-11-19。

页面：[Open Source - SentrySearch](https://km.xpaas.lenovo.com/spaces/LATC/pages/727107319/Open+Source+-+SentrySearch)，版本 8，最后更新 2026-11-12 22:03:38（UTC+8）。

代码依据：`C:\Users\luoyw\work\github\sentrysearch`，上游提交 `e99605eb000b018217803819cd3778d6f4636006`；另核对本地添加的 Sphere 评估脚本与 2B/CUDA 实测记录。结论限定于这份检出的代码；页面如引用其他分支或另行开发的 Web 扩展，应提供相应源码和提交。

## 总体判断

| 部分 | 判断 | 主要理由 |
|---|---|---|
| 1. Modules | 基本准确，建议细化存储表述 | 模块职责与源码一致；Chroma 保存向量及元数据，除了路径、时间窗，还包括 indexed_at 等字段。 |
| 2. Backends | 基本准确，建议补充重排支持范围 | 五类后端及 BaseEmbedder 接口正确；本次仅验证 Local/2B/CUDA。后端与模型的索引隔离属实，但不同后端的重排支持不相同。 |
| 3. What a result is | 核心机制准确，服务能力段落需修正 | 固定窗口、一窗一向量、不预测事件边界、异常度 Highlights 均有代码依据；Web 服务描述在当前版本缺少实现，评估脚本属于本地扩展。 |
| 4. Where moment times come from | 算法、返回时间和示例准确，建议增加两项技术说明 | 窗口算法与源码一致；padding 不修改返回时间。重排输入同样带 padding；索引切片也使用 stream copy，实际媒体边界可能受关键帧影响。 |

## 1. Modules

表格中的 Entry、Video、Models、Storage、Retrieval、Output 分工正确。

原句：`ChromaDB stores vectors and locations only.`

建议改为：

> Video files remain on disk. ChromaDB stores embeddings and metadata, including the source path, window start/end, and indexing timestamp; it does not store the video payload.

依据：[store.py](../sentrysearch/store.py#L279) 的 `add_chunk` 同时保存 `source_file`、`start_time`、`end_time`、`indexed_at`，并允许额外元数据。`_collection_name` 按后端及支持的模型配置区分集合；Gemini 使用固定的 `dashcam_chunks` 集合和固定 `gemini-embedding-2` 模型，另保留旧版 Local 集合兼容逻辑。“每个 backend/model 一个 collection”作为总体描述可保留，不宜解读为所有后端都可任意切换模型。

## 2. Backends

以下内容准确：

- `BaseEmbedder` 定义 `embed_video_chunk`、`embed_query`、`embed_image`、`dimensions`。
- `get_embedder` 支持 Gemini、Qwen Cloud、Local、MLX、LiteLLM。
- 当前实测记录确为 Local / Qwen3-VL-Embedding-2B / CUDA。
- 查询需使用与已建索引相容的后端及模型；CLI 会自动识别已有索引，显式选择无数据的配置时给出提示。

建议补充：

> Optional VLM reranking is available for Local, Gemini, and Qwen Cloud searches. MLX and LiteLLM do not support `--rerank` in the reviewed version. The reranking model can differ from the embedding backend: Qwen Cloud uses the Gemini reranker.

依据：`sentrysearch/base_embedder.py:6`、`embedder.py:19`、`store.py:28`、`cli.py:228`。云端、MLX 等后端的源码支持不等于已完成这些配置的精度实测。

## 3. What a result is

### 3.1 服务能力段落在当前版本没有依据

原文声称存在 `ThreadingHTTPServer`、HTML/JS 页面、GPU 查询串行锁、HTTP Range 播放及索引 allowlist，引用 `web.py` 和 `web.html`。

核查结果：

- 当前工作区没有 `web.py`、`web.html`；文件名搜索没有发现对应 Web 文件。
- `git ls-files sentrysearch scripts` 中没有这两个文件。
- 当前 `sentrysearch` 与 `scripts` 中没有 `ThreadingHTTPServer` 或对应播放服务实现；检索到的 Range 请求属于下载脚本，threading.Lock 属于下载或 API 限速，不能作为 GPU HTTP 查询锁的证据。
- 对上述 Web 路径执行 `git log --all -- ...` 未发现本地可见历史记录。该结果不代表检查了所有远端分支。
- `scripts/evaluate_sphere.py` 确实存在，但为本地新增、尚未跟踪的评估扩展；它使用子进程执行评估、文件锁防止重复评估，未提供所声称的 HTTP 服务。

建议删除当前没有源码支持的 Web 能力描述；若确有另一份实现，应明确标注为另行开发的扩展，并补充仓库、分支及提交。评估适配应独立描述为：

> For this evaluation, a locally added `scripts/evaluate_sphere.py` adapts Sphere releases, validates video SHA-256 and size, runs SentrySearch retrieval, and calls Sphere's reference scorer. This adapter is an evaluation extension rather than an upstream SentrySearch feature.

### 3.2 “The model chooses the window”不够准确

Local 模型把视频窗口和查询转换为向量；`SentryStore.search` 调用 Chroma 的向量查询，`search.py` 按相似度排序并可去重；可选 VLM 再对已有候选重排。Embedding 模型并未直接输出被选中的窗口 ID。

建议改为：

> The embedding model represents each window and the query as vectors. Chroma retrieves the nearest windows, and optional VLM reranking changes their order. Returned timestamps remain the indexed window's saved start and end.

依据：`local_embedder.py:283`、`store.py:335`、`search.py:15`、`reranker.py:108`。

### 3.3 “no reliable no result decision”需要限定证据范围

系统有低置信机制，并非没有任何拒答相关处理：

- 默认相似度阈值为 0.41，MLX 为 0.35，可由用户设置。
- 启用重排且返回有效分数时，`rerank_match=false` 会触发低置信提示。
- 自动导出遇到低置信结果时会询问用户；`--no-trim` 仍展示候选，仅加低置信提示。
- 本次 Local/2B、未启用重排的测试中，数据集 1 和 2 的原始返回误报率均为 100%；按 0.41 阈值诊断分别为 100% 与 75%。这支持本次配置拒答不可靠的判断，不能外推为所有后端和设置都没有可靠拒答能力。

建议改为：

> The CLI provides low-confidence prompts based on similarity thresholds and, when enabled, VLM match judgments. Reliable rejection of unanswerable queries has not been demonstrated by the current Local/2B evaluation; `--no-trim` retains candidates even when confidence is low.

依据：`cli.py:28`、`cli.py:1067`、`evaluation/results/dataset1.json`、`evaluation/results/dataset2.json`。两个数据集的 no-result 样本分别只有 3 和 8 条，结论应保留配置与样本范围。

### 3.4 其余核心描述可保留

- Local 以 1 FPS、最多 32 帧采样，并对整窗生成一个向量；检索返回保存的窗口时间。
- Reranker 输出匹配布尔值与置信度并调整排序，没有事件边界输出字段。
- Highlights 支持 centroid、knn、lof 三种可选算法；默认 knn。它们衡量的是向量异常度，不是人工认可的高光价值，也不改变窗口边界。原文建议将 `and LOF` 改为 `or LOF`，避免让人以为三者固定组合使用。
- Python CLI、增量索引、重试、临时文件清理，以及当前代码未提供分布式调度、多租户和完整服务治理的总体描述成立。

## 4. Where moment times come from

页面伪代码与 `chunker.py:177` 的 `expected_chunk_spans` 一致。实际调用该函数核验：

| D | L | O | 实际返回 |
|---:|---:|---:|---|
| 62 | 30 | 5 | (0,30)、(25,55)、(50,62) |
| 55 | 30 | 5 | (0,30)、(25,55) |
| 30 | 30 | 5 | (0,30) |

页面列出的 62 秒视频三组导出请求范围 0–32、23–57、48–62 也与 `trimmer.py:11` 的默认前后各 2 秒 padding 及文件时长截断相符。36–42 秒事件若命中 B，返回逻辑窗口为 25–55 秒，示例正确。

建议补充两个实现细节：

1. **重排输入也带 padding。** `reranker.py:127` 调用 `trim_clip` 时没有指定 padding，因此使用默认前后各 2 秒。VLM 看到的物理候选片段可能包含窗口外的画面，但 `start_time/end_time` 保持不变。建议写：`The reranker evaluates a padded candidate clip (default ±2 seconds), while returned window timestamps remain unchanged.`
2. **区分逻辑时间和实际媒体边界。** 不仅导出，`chunker.py:243` 的索引切片同样使用 `-ss` 和 `-c copy`。实际物理片段可能受关键帧寻址影响；页面导出表格应被理解为请求的裁剪范围，而非帧精确的实际输出保证。

这两项细节不改变页面的核心结论：返回的是预设索引窗口，而非模型预测的可变长度事件边界。

## 建议优先级

1. 优先修正第 3 部分未经当前源码支持的 Web 服务描述，并标清 Sphere 适配脚本来源。
2. 修改“模型选择窗口”和 No-result 的表述，补充重排支持范围。
3. 细化 Chroma 元数据、Highlights 三种算法可选关系、重排 padding 和逻辑/物理边界说明。
