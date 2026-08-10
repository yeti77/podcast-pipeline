# Podcast Pipeline

一个面向个人知识工作流的每周播客监控系统。它按固定业务周窗口抓取 RSS、
整理 Show Notes、评分并生成 Markdown 周报，也可以选择性地通过 OpenClaw 翻译
英文 Show Notes，并将报告幂等投递到飞书。

项目默认配置适合安全评估和本地开发：不会默认启用 OpenClaw 翻译，也不包含
任何飞书凭证、运行输出或个人缓存。

## 功能概览

- 读取 YAML 中配置的公开播客 RSS。
- 按 Asia/Shanghai 时区的完整业务周窗口筛选节目。
- 基于标题、Show Notes、时长和兴趣配置生成结构化评分。
- 输出 `screening_result.json` 和 Markdown 周报。
- 过滤 Show Notes 中常见的广告、订阅 CTA 和页脚内容。
- 可选地通过 OpenClaw agent 将英文 Show Notes 翻译为中文。
- 使用内容哈希和版本化 cache 避免重复翻译。
- 翻译失败时回退到过滤后的原文，不阻断周报。
- 可选地创建飞书文档并发送飞书群通知。
- 使用 run metadata 保证飞书投递和通知幂等。
- 提供不访问外部服务的完整安全回归入口。

## 系统边界

这个项目是本地自动化流水线，不是托管服务，也不是通用播客客户端。

- RSS、OpenClaw 和飞书都属于显式启用的外部边界。
- 评分用于排序和辅助判断，不代表投资或内容建议。
- 默认 `selection_policy.mode` 是 `all_preview`，评分不会自动删除有效节目。
- 原始 RSS 和 Show Notes 数据保留在结果 JSON；展示过滤和翻译只作用于报告层。
- 转写和人工选择链路是可选能力，不是每周筛选主链的必要条件。

## 环境要求

- Python 3.9 或更新版本
- Git
- `curl`
- 可选：OpenClaw，用于 Show Notes 翻译和部分嘉宾背景能力
- 可选：飞书应用和群机器人，用于文档投递与通知
- 可选：`ffmpeg`、Whisper 或 MLX Whisper，用于后续音频处理

核心 Python 依赖只有 PyYAML。转写依赖单独维护。

## Quick Start

```bash
git clone <your-repository-url> podcast_pipeline
cd podcast_pipeline

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt

python3 scripts/run_safe_regression.py
```

安全回归使用 fixture、临时目录和 fake subprocess，不会运行真实 RSS、OpenClaw
或飞书调用。

随后检查并调整：

- `config/podcasts.yaml`：RSS 监控源
- `config/interests.yaml`：关注主题、关键词和人物
- `config/policy.yaml`：公共默认策略
- `config/podcast_hosts.yaml`：嘉宾识别中的主持人排除提示

生产运行前先检查 cron wrapper 的有效路径和策略：

```bash
PODCAST_SCREENER_CRON_DRY_RUN=1 \
PODCAST_PIPELINE_PROXY=off \
bash scripts/podcast_screener_cron.sh
```

这条命令只打印诊断，不执行 screener、OpenClaw 或飞书步骤。

## 配置本机覆盖

受控的 `config/policy.yaml` 保持公开安全默认：

```yaml
show_notes_translation:
  enabled: false
  mode: mock
```

如需在本机启用 OpenClaw，复制被 Git 忽略的覆盖文件：

```bash
cp config/policy.local.example.yaml config/policy.local.yaml
```

运行时会递归合并 `policy.local.yaml` 和公共 policy。不要在任何 policy YAML 中
保存账户 token；OpenClaw 的登录态由 OpenClaw 自己管理。

当前 OpenClaw runner 使用非交互命令：

```text
openclaw agent --agent <agent_id> --message <prompt> --json --timeout <seconds>
```

本机必须先单独验证 OpenClaw CLI、agent 和模型账户状态。项目不会自动配置或
登录 OpenClaw。

详见 [配置说明](docs/configuration.md)。

## 飞书配置

复制示例文件并在本机填写：

```bash
cp config/feishu_config.example.json config/feishu_config.json
cp config/feishu_folder_mapping.example.json config/feishu_folder_mapping.json
```

也可以使用环境变量：

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
FEISHU_WEBHOOK_URL
```

环境变量优先于本地 JSON。真实配置文件均被 `.gitignore` 排除。

在已有筛选结果时，可以先验证飞书 renderer 和输入：

```bash
python3 scripts/deliver_weekly_report_to_feishu.py --dry-run
python3 scripts/feishu_notify.py --dry-run
```

`--dry-run` 不调用飞书 API，也不写 delivery/notification 状态。

## 运行方式

### 只生成本地周报

下面的命令会真实抓取 RSS，并根据本机 policy 可能调用 OpenClaw：

```bash
python3 scripts/podcast_screener.py
```

指定日期用于复验某个业务周：

```bash
python3 scripts/podcast_screener.py --run-date 2026-08-09
```

### 完整自动链路

```bash
bash scripts/podcast_screener_cron.sh
```

wrapper 按顺序执行：

1. `podcast_screener.py`
2. `deliver_weekly_report_to_feishu.py`
3. `feishu_notify.py`

任一步失败都会阻止后续步骤。请先完成 dry-run，并确认 RSS、OpenClaw、飞书和
代理配置后再运行真实链路。

## 业务周窗口

默认业务周使用 Asia/Shanghai 时区，从周日 22:00 到下一周日 22:00。自动任务
应在窗口结束后触发，避免把尚未完整结束的周误当成正式报告。

调度配置属于本机运维状态，不提交到仓库。macOS 可通过 launchd 调用 cron
wrapper；其他系统可以使用自己的任务调度器。

## 输出与状态

每次运行写入独立目录：

```text
outputs/runs/{week_id}/{run_id}/
  screening_result.json
  screening_report.md
```

`outputs/latest` 及顶层 latest 文件指向最近的完整 run。以下目录都是本地运行
状态，不应提交：

```text
outputs/
state/
cache/
logs/
download/
transcripts/
whisper_output/
```

结果字段说明见 [数据结构](data_schema.md)。

## Show Notes 翻译

展示层翻译流程为：

```text
raw show_notes_text
  -> display filter
  -> language detection
  -> versioned cache lookup
  -> paragraph-aware chunking
  -> translation runner
  -> quality and URL preservation checks
  -> Markdown / Feishu renderer
```

重要行为：

- 中文、混合或无法判断的文本通常不翻译。
- sponsor/footer block 在进入翻译 cache key 和 runner 前过滤。
- cache key 包含过滤后文本的 hash 和 translation version。
- runner 失败、结果不完整或质量检查失败时回退到过滤后的原文。
- 缺失的源 URL 会在译文末尾以“原文链接”补回。
- 原始 `show_notes_text` 仍保留在 JSON 中。

## 飞书幂等

- 已成功创建文档的 run 默认不会重复创建；显式 `--force` 才会重建。
- 已成功通知的 run 默认不会重复发群消息；显式 `--force` 才会重发。
- 存在失败或不完整 metadata 时，命令会停止并要求人工检查。
- 通知依赖成功的文档投递 metadata，不会猜测文档地址。

## 故障排查

先运行：

```bash
python3 scripts/run_safe_regression.py
PODCAST_SCREENER_CRON_DRY_RUN=1 bash scripts/podcast_screener_cron.sh
```

常见检查顺序：

1. 确认 `PODCAST_PIPELINE_HOME` 和 Python 路径正确。
2. 确认代理配置可用；直连时设置 `PODCAST_PIPELINE_PROXY=off`。
3. 检查 RSS URL 是否仍返回可解析 XML 和 enclosure。
4. 检查有效 policy 是否启用了预期模式和 agent。
5. 检查 OpenClaw CLI 是否可从非交互环境调用。
6. 检查 run JSON 中的 runtime、Show Notes 和 translation metadata。
7. 检查飞书 folder mapping、应用权限和 delivery metadata。

详细说明见 [运维指南](docs/operations.md)。

## 项目结构

```text
config/                  公共配置和本地配置示例
docs/                    架构、配置与运维文档
scripts/                 主流程、renderer、adapter、cache 和测试
.github/workflows/       无外部副作用的 CI
data_schema.md           结果 JSON 数据结构
```

主要模块边界见 [架构说明](docs/architecture.md)。

## 贡献

提交行为变化时应补充 hermetic tests，并确保：

```bash
python3 scripts/run_safe_regression.py
```

完整通过。CI 不应访问真实 RSS、OpenClaw、MiniMax、飞书或本地运行目录。

参见：

- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [行为准则](CODE_OF_CONDUCT.md)
- [变更记录](CHANGELOG.md)

## License

[MIT](LICENSE)
