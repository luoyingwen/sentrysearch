# 四数据集评估

本机使用 Python 3.12、RTX 3090、CUDA 12.4 PyTorch 和项目原生本地
Qwen3-VL-Embedding-2B 后端（用户选择优先完成速度）。无须 API Key。默认设置保持项目的 30 秒切片、
5 秒重叠、480p / 5 FPS 预处理、静止跳过、0.9 向量去重，不加重排。

Windows 已在项目 `.env` 配置 `FORCE_QWENVL_VIDEO_READER=torchvision`，使用
PyAV 解码；锁定版本的 torchcodec 在本机无法加载共享 FFmpeg DLL。
修复了 `local_embedder.py` 传递 `file://C:\...` 导致 PyAV 返回空帧的问题，
改用读取器支持的本地绝对路径。

首次安装（在项目目录执行）：

```powershell
uv sync --locked --extra local --group test
.\.venv\Scripts\sentrysearch.exe --help
.\.venv\Scripts\python.exe -m pytest
```

若首次大文件下载过慢，可先用基础环境中的 Hugging Face 客户端运行可续传
下载辅助脚本。它从官方 ModelScope 镜像取 Qwen 权重，从上海交大镜像取
PyTorch wheel，最终分别核对 Hugging Face 固定版本 SHA-256 和 `uv.lock`
锁定 SHA-256，验证通过才安装或交给模型加载：

```powershell
.\.venv\Scripts\python.exe scripts\download_evaluation_runtime.py torch
.\.venv\Scripts\python.exe scripts\download_evaluation_runtime.py model --model-size 2B --workers 32
uv pip install --python .venv\Scripts\python.exe --no-index --find-links evaluation\download-cache --no-deps "torch==2.6.0+cu124"
uv sync --locked --extra local --group test
```

执行所有评估：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_sphere.py
```

日常使用本机已下载的 2B 模型：

```powershell
.\.venv\Scripts\sentrysearch.exe index "视频目录" --backend local --model qwen2b
.\.venv\Scripts\sentrysearch.exe search "查询描述" --backend local --model qwen2b --no-trim
```

脚本启动前先核对 `evaluation/completion.json`。两个目标已完成且项目源码、
评分脚本、输入标注、ZIP 身份、模型配置和依赖版本没有变化时直接退出；
`--force` 显式重新测试。操作系统文件锁防止周期任务重叠运行。

只验证资源完整性（不产生精度结果）：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_sphere.py --preflight-only --workspace evaluation-preflight
```

输出：`evaluation/report.md` 为中文总报告；`evaluation/results/dataset1.json`
至 `dataset4.json` 包含逐查询预测、GT、分数、对齐证据和分组指标；
`environment.json` 记录实际文字/视频推理、CUDA 和 Chroma 查询验证；
`completion.json` 记录全任务状态和复现指纹。`aligned_only` 或 `failed` 均不会
被识别为完成。

开始每个新数据集前，删除本评估目录 `active` 中全部旧媒体、临时切片和索引；
每个数据集结束后再次删除。子进程退出后才删 Chroma 文件，避免 Windows 文件
句柄占用。删除目标必须是已解析工作区下的精确 `active` 目录。输入 ZIP、原始
标注、模型权重、审核报告不是历史检索数据，予以保留。

实际视频使用完整 SHA-256 和大小一对一对齐，允许 `_LL360` 等改名，不依赖
模糊文件名匹配。任一媒体不一致会跳过整套数据，报告具体原因；时间范围额外
校验，容许 0.5 秒的容器与标注舍入差异，不改变 GT 或时间轴。外部路径可用
`--sphere-root`、`--videos-root` 设置。

QVHighlights 的 CardSet 时长可能按名义片段长度填写，不能据此要求它与容器
时长相等。例如一段清单为 124 秒的视频实际约 122.97 秒，而所有 GT 在
110 秒内；完整哈希相同且 GT 仍在实际媒体范围内，所以该视频可以评分。

评分直接加载原仓库 `sphere_eval/eval/metrics.py`，未复制近似实现。排名评估
取 Top-10，moment 要求 tIoU ≥ 0.5，asset 不要求时间窗匹配。返回窗口与 GT
一对一分配，R@K 分母为 min(K, GT 数)，mean tIoU 未匹配 GT 计零。
no_result 排除在排名均值之外并单独计算误报。CLI 的低置信提示不隐藏
`--no-trim` 返回值，故主要分数保留低置信结果，并另外报告 0.41 阈值下的
no_result 误报率。

Card 描述和 GT 不进入检索模型；不按 GT 改切片或调参。短 GT 和跨较长时间
的 GT 可能因为固定切片而低于 tIoU 阈值，此结果反映该默认配置的定位能力。
