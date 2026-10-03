# `pipeline/` 代码解释

`POC/pipeline/` 是教育短视频 Demo 的核心处理包：把一节长讲座视频变成 **3–5 条主题相关片段**，再拼成一条带片头、字幕和片尾 CTA 的合辑。

Gradio 入口在 `POC/app.py`。本目录只负责算法与媒体处理，不包含 UI。

设计原则：

- **先文本、后切视频**：用转写找片段，人工确认后再 ffmpeg 裁切。
- **模型只提名，人不放行不导出**：embedding 只排序，不决定最终成片。
- **导出物是一条合辑**，不是多条独立短视频。原始裁切留在工作目录，不对外交付。

---

## 1. 流水线全貌

```text
长视频 + 教学主题 + 学习目标 + 片源 URL
        │
        ▼
[transcribe]  WhisperX → transcript.json / transcript.srt
        │
        ▼
[nominate]    Ollama (qwen2.5) → 8–12 条候选片段
        │
        ▼
[rank]        MiniLM 余弦相似度 → Top 10 给人审
        │
        ▼
[app.py]      人工勾选 3–5 条，可改 start/end/title
        │
        ▼
[cut]         ffmpeg 按时间轴切 raw_clips/*.mp4
        │
        ▼
[assemble]    片头卡 + 归一化片段 + 烧字幕 + 片尾卡
        │
        ▼
[export]      compilation.mp4 + captions.srt + post.txt + zip
```

对应文件：

| 文件 | 职责 |
|------|------|
| `__init__.py` | 包标记，无逻辑 |
| `utils.py` | 路径、常量、ffmpeg/ffprobe 封装、字幕与时间工具 |
| `transcribe.py` | WhisperX 转写 + 词级对齐 |
| `nominate.py` | Ollama 提名候选片段 |
| `rank.py` | SentenceTransformer 按学习目标打分 |
| `cut.py` | 按时间裁切预览 / 原始片段 |
| `assemble.py` | 合辑：片头、归一化、字幕 overlay、片尾 |
| `package.py` | 单条短视频包装（竖屏、标题卡、loudnorm）；**当前导出路径未调用** |
| `export.py` | 编排 cut + assemble，写出交付目录和 zip |

---

## 2. 一次运行的目录

每次点击「Run transcription」会生成 10 位 hex `run_id`：

```text
POC/
  work/{run_id}/
    source.mp4              # 上传视频的副本
    transcript.json
    transcript.srt
    previews/{id}.mp4       # 审片用预览
    raw_clips/{id}.mp4      # 确认后的原始裁切（不导出）
    assemble/               # 合辑中间文件（intro / norm / captions）
  exports/{run_id}/
    compilation.mp4
    captions.srt
    post.txt
    selection.json
    compilation.zip
```

常量在 `utils.py`：

| 常量 | 值 | 含义 |
|------|----|------|
| `MAX_REVIEW` | 10 | 给人审的候选上限 |
| `MIN_KEEP` / `MAX_KEEP` | 1 / 5 | 必须保留的片段数，否则不能导出 |

---

## 3. 核心数据结构

### 转写片段 `segment`

```python
{
  "start": 5.915,
  "end": 8.240,
  "text": "...",
  "words": [{"word": "...", "start": ..., "end": ...}, ...]  # 对齐成功才有
}
```

`transcript.json` 再包一层：`{"language": "en", "segments": [...]}`。

### 候选 / 保留片段 `clip`

提名、排序、人工确认后共用同一套字段：

```python
{
  "id": "c1",
  "start": 12.3,
  "end": 38.1,
  "title": "短标题 / 知识点",
  "takeaway": "一句话知识点",
  "reason": "为何独立成片",
  "quote": "转写摘句",
  "source": "ollama" | "window",   # 来自 LLM 还是滑窗兜底
  "score": 0.73,                   # rank 后才有
  "excerpt": "该时间范围内的转写原文"
}
```

时间会经 `snap_to_segments()` 吸附到转写句边界，避免切在句子中间。

---

## 4. 模块说明

### 4.1 `utils.py` — 公共工具

媒体与 IO：

- `new_run_id()` / `run_dir()`：创建本次工作目录（含 `previews`、`raw_clips`、`exports`）。
- `as_video_path()`：兼容 Gradio 的字符串 / dict / 文件对象。
- `save_json()` / `to_jsonable()`：把 numpy scalar 等转成可 JSON 序列化对象。
- `write_srt()` / `format_timestamp()`：SRT 时间码，格式 `HH:MM:SS,mmm`。

ffmpeg：

- `ffmpeg_bin()` / `ffprobe_bin()`：找不到就立刻报错，避免后面静默失败。
- `run_cmd()`：捕获 stderr，失败时抛出最后 2000 字符。
- `media_duration()` / `media_size()` / `has_audio()`：探测时长、偶数宽高、是否有音轨。

文本与时间：

- `parse_objectives()`：学习目标按行拆开，去掉 `-` / `•`。
- `excerpt_for_range()`：取出 `[start, end]` 内的转写句子。
- `snap_to_segments()`：把裁切点扩到覆盖该区间的第一句起点 / 最后一句终点；若完全对不上，就用最近的一句。
- `ff_text()` / `ff_subtitles_path()`：转义 ffmpeg `drawtext` / `subtitles` 滤镜里的特殊字符。
- `find_font()`：按 macOS 常见路径找 Arial / PingFang，供字幕和卡片用。

### 4.2 `transcribe.py` — 语音转写

入口：`transcribe_video(video_path, work) -> dict`。

流程：

1. 懒加载 WhisperX `small` 模型（`float32`）。
2. 设备选择：有 CUDA 用 `cuda`；**有 MPS 也强制 `cpu`**（WhisperX 在 Apple GPU 上不稳定）。
3. `transcribe` 得到句级片段和语言码。
4. 按语言加载对齐模型，做词级 `align`；失败则退回句级结果。
5. 丢掉空文本、没有起止时间的词。
6. 写出 `work/transcript.json` 和 `work/transcript.srt`。

`transcript_as_text()` 把片段格式化成：

```text
1. [5.915-8.240] 原句
```

供 LLM 提名使用。模型实例缓存在模块全局变量里，避免每次请求重新加载。

### 4.3 `nominate.py` — LLM 提名

入口：`nominate_candidates(model, topic, objectives, segments, min_s, max_s, duration)`。

系统提示词来自 `POC/prompts/nominate_clips.txt`，运行时把 `MIN_SECONDS` / `MAX_SECONDS` 替换成滑条值。规则要点：

- 提名 8–12 条
- 一条一个知识点、可独立看懂
- 跳过开场寒暄、广告、回顾前文
- 时间必须来自转写，不要编造
- 主题匹配优先于「钩子」

实现细节：

1. **分块** `_chunk_segments()`：按约 9000 字符切转写，块与块重叠最后 2 句，避免知识点被切断。
2. 每块调用 `ollama.chat(..., format="json")`。
3. `_parse_json()`：去掉 markdown 代码围栏，必要时用正则抽出第一个 `{...}`。
4. `_normalize_candidates()`：
   - 吸附句边界
   - 夹到 `[0, duration]`
   - 过短（`< min_s * 0.6`）丢弃；过长截到 `max_s` 再吸附
   - 按 `(start, end)` 去重，id 重编为 `c1, c2, ...`
5. 有效候选不足 3 条时，`_window_fallback()` 按最短时长滑窗硬切若干条（`source: "window"`），保证审片页不空。
6. 仍为空则抛错，并附上最后一次 Ollama 异常。

`list_ollama_models()` 给 Gradio 下拉框用：优先 `qwen2.5`。

### 4.4 `rank.py` — 学习目标打分

入口：`rank_candidates(candidates, objectives, segments, top_n=10)`。

- 模型：`sentence-transformers/all-MiniLM-L6-v2`（懒加载）。
- 目标向量：对每条 learning objective 编码；若用户没填，用占位句 `"educational lecture content"`。
- 候选文本：`title + takeaway + quote + 该时间范围转写 excerpt`。
- 分数：候选向量与所有目标向量的 **最大余弦相似度**（命中任意一条目标即可）。
- 降序后截断 Top N。**不删低分条，只排序**，最终去留由人决定。

### 4.5 `cut.py` — 裁切

`cut_clip(source, start, end, dest, copy=False)`：

```text
ffmpeg -ss {start} -to {end} -i source
```

- `copy=True`：先尝试流拷贝（快，可能切在关键帧上不准）；失败再重编码。
- 默认 / 失败路径：`libx264 veryfast` + `aac` + `yuv420p`。
- 最短保留 0.2 秒，避免零时长输出。

`make_preview()` 就是 `copy=False` 的 `cut_clip()`，给审片页用。导出阶段的 raw 裁切用 `copy=True`。

### 4.6 `assemble.py` — 合辑合成

入口：`assemble_compilation(raw_clips, dest, ...)`。

顺序：

1. 以第一条 raw clip 的分辨率作为合辑画幅（保证偶数）。
2. `make_intro_card()`：PIL 画深色卡片（标题 + 副标题）→ 循环静帧成 1–2 秒视频，配静音轨。
3. `_normalize_clip()`：每条片段 `scale+pad` 到同一宽高、30fps、yuv420p、AAC 44.1k stereo；无音轨则补 `anullsrc`。
4. `make_end_card()`：Key takeaway + CTA，2–6 秒。
5. `concat_mp4s()`：ffmpeg concat demuxer，先 `-c copy`，失败再重编码。
6. `compilation_cues()`：把每条 clip 的转写时间映射到合辑时间轴（加上片头时长和前面片段时长）。
7. 写出 `captions.srt`（若提供 `srt_path`）。
8. `burn_captions()`：每 8 条 cue 一批，把字幕 PNG overlay 到底部；多批串行，避免一条超长 filter graph。

字幕条是黑底白字 PNG，不是 ffmpeg `subtitles` 滤镜。片头/片尾期间没有字幕 cue。

### 4.7 `export.py` — 交付编排

入口：`export_clips(...)`。

闸门：`len(clips)` 必须在 3–5，否则直接报错。

然后：

1. 清空并重建 `exports/{run_id}/`。
2. 对每条保留片段 `cut_clip(..., copy=True)`，写入 `work/raw_clips/`。
3. 片头标题默认用第一条 clip 的 title；片尾 takeaway 默认把各 clip 的 takeaway 拼成项目符号列表。
4. 调用 `assemble_compilation()`。
5. 写 `post.txt`（标题、takeaway、CTA、片源 URL、各段原始时间码）和 `selection.json`。
6. 打 `compilation.zip`（mp4 + srt + post + json）。

返回：`([compilation.mp4], post_text, zip_path, compilation_path)`。

默认 CTA：

> Full discussion in the original lecture. Link in the description.

片上不烧 URL，链接只出现在 `post.txt`。

### 4.8 `package.py` — 单条包装（现存未接入）

`package_clip()` 面向「每条 clip 单独成片」：

- 可选竖屏：`scale+crop` 到 1080×1920
- 前 2 秒 `drawtext` 标题卡
- 最后 2 秒 CTA
- `subtitles=` 烧录该片段的 SRT 切片
- `loudnorm` 到约 -14 LUFS
- 滤镜失败则退回简单缩放重编码

`slice_srt()` 把全局转写裁成相对该 clip 起点的时间轴。

当前 `app.py` → `export_clips()` 走的是 **一条合辑**，没有调用本模块。若以后要恢复「多条独立短视频」交付，应从这里接回。

---

## 5. 与 `app.py` 的衔接

| Tab | 调用 | 说明 |
|-----|------|------|
| 1. Input | `transcribe_video` | 校验 ffmpeg / 主题 / 学习目标后转写 |
| 2. Transcript | `nominate_candidates` → `rank_candidates` → `make_preview` | 提名、打分、生成审片预览 |
| 3. Review | `snap_to_segments` + `excerpt_for_range` | 改时间后吸附句边界；勾选 3–5 条才能 Confirm |
| 4. Package | `export_clips` | 必须先 `confirmed=True` |

`platform` 下拉（tiktok/shorts/reels/bilibili）目前只写入 state，**不改变裁切或合辑画幅**。合辑分辨率跟随源视频，不是强制 9:16。

---

## 6. 外部依赖

来自 `POC/requirements.txt` 以及系统工具：

| 依赖 | 用途 |
|------|------|
| WhisperX + PyTorch | 转写与词级对齐 |
| Ollama Python SDK + 本地 `qwen2.5` | 提名候选 |
| sentence-transformers | 学习目标相似度 |
| Pillow | 片头/片尾/字幕条位图 |
| ffmpeg / ffprobe | 裁切、探测、合辑、烧字幕 |
| Gradio | UI（本目录外） |

模型首次运行会下载权重，转写第一步通常最慢。

---

## 7. 失败与兜底

| 场景 | 行为 |
|------|------|
| 无 ffmpeg / ffprobe | 立即 `RuntimeError` |
| WhisperX 对齐失败 | 退回未对齐的句级片段 |
| Ollama 某块失败 | 记下 `last_error`，继续下一块 |
| LLM 候选 < 3 | 滑窗兜底再规范化一遍 |
| 全部候选无效 | 抛错并附上最后一次 Ollama 错误 |
| 流拷贝裁切失败 | 重编码 H.264/AAC |
| concat `-c copy` 失败 | 重编码后再拼接 |
| `package_clip` 滤镜失败 | 去掉标题/字幕，只做偶数缩放 |
| 导出时不是 3–5 条 | 拒绝导出 |

---

## 8. 阅读顺序建议

1. `utils.py`：常量和时间吸附
2. `transcribe.py` → `nominate.py` → `rank.py`：找片段
3. `cut.py` → `assemble.py` → `export.py`：做成片
4. 对照 `POC/app.py` 看人工确认闸门
5. `package.py` 仅在需要单条竖屏短视频时再看
