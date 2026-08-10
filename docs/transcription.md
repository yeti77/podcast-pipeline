# 本地 Whisper 转写

这是每周播客筛选主链之外的可选工具。它把一个已经存在于本机的音频文件转成
TXT、SRT、VTT 和 JSON metadata，供人或任意 Agent 后续阅读和处理。

本项目不下载音频，也不会从 RSS enclosure、飞书消息或周报中自动选择和获取
节目。请确保你有权保存和转写输入内容，并由你或自己的 Agent 提供明确的本地
文件路径。

## 安装

建议沿用项目的 Python 虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-transcription.txt
```

转写还需要 `ffmpeg` 和 `ffprobe`。下面只是常见安装示例：

```bash
# macOS / Homebrew
brew install ffmpeg

# Debian / Ubuntu
sudo apt-get update
sudo apt-get install ffmpeg
```

`requirements-transcription.txt` 提供两种本地后端：

- Apple Silicon 可使用 MLX Whisper，通常速度更合适。
- 其他平台可使用 OpenAI Whisper；CPU 转写通常明显更慢。

安装包不等于已经下载模型。第一次真正转写时，后端可能下载所选模型，并占用
较多时间、内存和磁盘空间。请先确认网络、剩余空间和模型许可符合你的环境。

## 环境检查

```bash
python3 scripts/podcast_transcriber.py --check
```

这条命令只检查平台、`ffmpeg`、`ffprobe` 和 Python 包可用性，输出一行 JSON。
它不处理音频、不加载或下载模型，也不创建转写输出目录。`selected_backend` 表示
本机在 `auto` 模式下会选择的后端；`configured_backend` 记录 policy 中的偏好。

## 转写一个本地文件

使用绝对路径最容易让人和 Agent 都明确输入、输出位置：

```bash
python3 scripts/podcast_transcriber.py \
  --audio /path/to/episode.mp3 \
  --output-dir /path/to/transcripts/episode \
  --backend auto \
  --language auto
```

`--audio` 只接受现有的本地普通文件；`http://` 和 `https://` URL 会被拒绝。
`--backend auto` 在兼容的 Apple Silicon 上优先 MLX，否则使用已安装的 OpenAI
Whisper。也可以显式传入 `mlx` 或 `openai`，显式后端失败时不会静默切换。

成功后 stdout 只有一行 JSON，`status` 为 `success`；同一音频 hash、后端、模型
和语言已有完整结果时，`status` 为 `reused`，不会重新加载模型。需要强制重跑时
增加 `--force`。

## 输出

指定目录中会原子发布四个文件：

```text
transcript.txt
transcript.srt
transcript.vtt
transcription_meta.json
```

- `transcript.txt`：连续纯文本，适合阅读、检索和总结。
- `transcript.srt`：SRT 字幕。
- `transcript.vtt`：WebVTT 字幕。
- `transcription_meta.json`：输入 hash、后端、模型、语言、耗时和输出路径。

CLI 退出码为：`0` 成功或复用，`2` 输入错误，`3` 环境缺失，`4` 后端转写失败，
`5` 输出写入失败。Agent 应解析 stdout JSON 的 `status`，不要仅根据文件名猜测
是否成功。

## 给 Agent 的通用指令

下面的提示词适用于已经能执行本机命令的 OpenClaw、Codex 或其他 Agent。这个
CLI 本身不会调用 OpenClaw，也不依赖特定 Agent：

```text
请只处理我指定的一个本地音频文件，不要下载其它音频，不要修改项目配置，也不
要上传音频或转写结果。先运行：
python3 scripts/podcast_transcriber.py --check
确认 stdout JSON 的 status 为 check_ok 后，再运行：
python3 scripts/podcast_transcriber.py --audio <绝对输入路径> --output-dir <绝对输出目录> --backend auto --language auto
解析 stdout JSON；只有 status 为 success 或 reused 才算完成。最后告诉我
transcript.txt、transcript.srt、transcript.vtt 和 transcription_meta.json 的路径，
如失败则报告 status、exit_code 和 stderr 摘要。
```

下载方式、节目选择和版权判断仍由用户与 Agent 自行决定。不要让 Agent 从周报
批量下载或转写未明确指定的节目。

## 隐私与本地文件

音频和转写内容可能包含受版权保护、敏感或个人信息。请遵守来源条款和所在地
法律，不要在没有授权时传播。推荐把输入和输出放在项目已忽略的 `download/`、
`transcripts/` 或其他不受 Git 跟踪的本地目录，并在提交前运行 `git status`。

## 故障排查

- `ffmpeg` 或 `ffprobe` 不可用：安装 `ffmpeg` 后重新运行 `--check`。
- 找不到 Whisper backend：重新安装 `requirements-transcription.txt`，确认当前
  shell 使用的是同一个虚拟环境。
- MLX 提示需要 Apple Silicon：改用 `--backend openai`，或在支持的 Mac 上运行。
- CPU 转写太慢：选择更小的 OpenAI Whisper 模型，或改到有合适加速的机器。
- 首次运行很久没有结果：检查模型下载、磁盘空间和进程资源占用。
- 输出为空或后端失败：检查音频能否被 `ffprobe` 读取，并查看 stderr 错误摘要。
- 返回 `reused`：当前输入和参数已有完整结果；确需重转时使用 `--force`。
- 旧结果不应被覆盖：发布失败会回滚四个受管理文件；请检查输出目录权限和空间。
