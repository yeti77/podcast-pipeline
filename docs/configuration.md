# Configuration

## Configuration Layers

The pipeline uses two policy layers:

1. tracked public defaults in `config/policy.yaml`
2. optional ignored operator overrides in `config/policy.local.yaml`

The local file is recursively merged over the tracked file. A nested local
setting overrides only that setting; sibling public defaults remain available.
All production consumers use the same loader.

Create a local override from the example:

```bash
cp config/policy.local.example.yaml config/policy.local.yaml
```

Never store account credentials in either policy file.

## Tracked Configuration

### `config/podcasts.yaml`

Defines the feed registry. Each entry identifies a podcast and its RSS URL.
Use public RSS endpoints without embedded credentials or private query tokens.

After adding a feed, validate it separately before a production run: check HTTP
status, XML parsing, item count, latest publication time, and audio enclosure.

### `config/podcast_hosts.yaml`

Contains host names and aliases used to avoid treating a regular host as an
episode guest. It is a detection hint, not an identity database.

### `config/interests.yaml`

Contains scoring topics, keywords, important people, and negative signals.
Changes affect ordering and suggestions, so update scoring tests when changing
the meaning of these fields.

### `config/policy.yaml`

Contains processing, scoring, selection, translation, advertisement detection,
Feishu, and transcription policy.

Important public defaults:

```yaml
selection_policy:
  mode: all_preview

show_notes_translation:
  enabled: false
  mode: mock
```

`all_preview` keeps valid episodes in the report and uses scores for ordering.
Switching to `score_based` changes selection behavior and should be reviewed as
a product decision.

## Show Notes Translation

The local example enables OpenClaw without changing public defaults:

```yaml
show_notes_translation:
  enabled: true
  mode: openclaw
  target_language: zh
  cache_enabled: true
  cache_dir: cache/show_notes_translations
  model: minimax-portal/MiniMax-M2.7
  agent_id: main
  max_chunk_chars: 1800
  timeout_seconds: 120
```

`model` is metadata for cache/version tracking; OpenClaw owns actual account and
model routing. The runner invokes the configured agent through:

```text
openclaw agent --agent <agent_id> --message <prompt> --json --timeout <seconds>
```

Before enabling it for an automated run:

1. confirm the CLI is available in the scheduler's PATH
2. verify the agent non-interactively on a small sample
3. use a dedicated cache directory for dry-runs
4. review translation completeness, URL preservation, and cost
5. confirm failure falls back to original Show Notes

Do not assume `openclaw web-search` exists. Guest search treats unavailable or
invalid OpenClaw search output as no results and continues through safe fallback
paths.

## Feishu Credentials

Copy the secret-safe examples:

```bash
cp config/feishu_config.example.json config/feishu_config.json
cp config/feishu_folder_mapping.example.json config/feishu_folder_mapping.json
```

`config/feishu_config.json` accepts:

```json
{
  "app_id": "",
  "app_secret": "",
  "webhook_url": ""
}
```

`config/feishu_folder_mapping.json` maps the weekly report destination:

```json
{
  "weekly_reports": {
    "feishu_folder_id": "",
    "feishu_folder_url": ""
  }
}
```

Credentials can instead come from:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_WEBHOOK_URL`

Environment values take precedence. Local JSON files are ignored. The project
can also read an existing OpenClaw Feishu channel configuration as a legacy
fallback, but explicit environment or local project config is easier to audit.

## Runtime Paths

The repository root is the default pipeline home. Supported overrides:

| Variable | Purpose |
| --- | --- |
| `PODCAST_PIPELINE_HOME` | Pipeline root |
| `PIPELINE_DIR` | Legacy root override |
| `PODCAST_PIPELINE_CONFIG_DIR` | Configuration directory |
| `PODCAST_PIPELINE_OUTPUT_DIR` | Run output directory |
| `PODCAST_PIPELINE_STATE_DIR` | Registry and queue state |
| `PODCAST_PIPELINE_LOG_DIR` | Runtime logs |
| `PODCAST_PIPELINE_PYTHON` | Python used by cron wrapper |
| `PODCAST_PIPELINE_EXTRA_PATH` | Additional scheduler PATH entries |

The repository does not automatically load `.env`. Use the shell, scheduler
environment, or another explicit secret manager.

## Proxy Configuration

The cron wrapper accepts:

- `PODCAST_PIPELINE_PROXY=off` for direct networking
- `PODCAST_PIPELINE_PROXY=<url>` for an explicit proxy
- `PODCAST_PIPELINE_ALL_PROXY=<url>` for the `all_proxy` value

Always inspect effective values with cron dry-run before enabling a scheduler.

## Local Files And Git

These files and directories must remain untracked:

```text
.env
config/feishu_config.json
config/feishu_folder_mapping.json
config/policy.local.yaml
outputs/
state/
cache/
logs/
download/
transcripts/
whisper_output/
```

Run `python3 scripts/test_repository_hygiene.py` before publishing changes to
configuration or repository metadata.
