# Project Instructions for Coding Agents

## Project Purpose

This repository implements a local weekly podcast monitoring pipeline. It
fetches configured RSS feeds, selects episodes within a completed business-week
window, scores them, renders Markdown, and can optionally translate Show Notes
through OpenClaw and deliver the report to Feishu.

The repository must remain usable without credentials or external services.
Tracked defaults are public and safe; operator-specific behavior belongs in
ignored local configuration.

## Safety Rules

- Never commit `.env`, account tokens, Feishu credentials, OpenClaw account
  data, private feed URLs, runtime outputs, logs, audio, transcripts, or cache.
- Never run production RSS, OpenClaw, Feishu, notification, or transcription
  commands unless the user explicitly requests that external action.
- Do not use production output or cache as a test fixture.
- Keep tests hermetic with temporary directories, fake subprocesses, and fake
  network adapters.
- Do not change the business-week window, selection mode, delivery semantics,
  or scheduler as part of an unrelated task.
- Preserve existing user changes in a dirty worktree.
- Use small commits with tests proportional to the behavioral risk.

## Public And Local Configuration

Tracked public configuration:

- `config/podcasts.yaml`
- `config/podcast_hosts.yaml`
- `config/interests.yaml`
- `config/policy.yaml`

Ignored operator configuration:

- `config/policy.local.yaml`
- `config/feishu_config.json`
- `config/feishu_folder_mapping.json`
- `.env`

`policy_config.py` recursively merges optional `policy.local.yaml` over the
tracked policy. All production consumers must use this shared loader so cron,
screener, rendering, and delivery observe the same effective policy.

The tracked Show Notes translation policy must remain safe for a public clone:

```yaml
show_notes_translation:
  enabled: false
  mode: mock
```

Do not place real secrets in a policy YAML. OpenClaw authentication remains
owned by OpenClaw, not this project.

## Module Ownership

- `pipeline_paths.py`: side-effect-free runtime path resolution.
- `rss_adapter.py`: RSS fetching, XML parsing, and raw Show Notes preservation.
- `episode_duration.py`: duration parsing and quality classification.
- `episode_show_notes_renderer.py`: display filtering and Show Notes sections.
- `show_notes_translation_*`: language detection, chunking, cache, runners, and
  orchestration.
- `guest_*`: guest source quality, cache, search, generation, and orchestration.
- `podcast_screener.py`: business window, scoring, run assembly, and Markdown.
- `feishu_blocks_renderer.py`: pure Feishu block rendering.
- `latest_result_store.py`: latest pointers and delivery/notification metadata.
- `deliver_weekly_report_to_feishu.py`: idempotent Feishu document delivery.
- `feishu_notify.py`: idempotent group notification.
- `podcast_transcriber.py`: optional one-file local Whisper transcription.
- `podcast_screener_cron.sh`: fail-closed production coordination.

Keep external calls at adapter or entrypoint boundaries. Pure renderers and
parsers must remain import-safe and free of filesystem side effects.

## Show Notes Contract

- `show_notes_text` in result JSON remains the original normalized RSS field.
- Sponsor/footer cleanup is display-only.
- Translation input and cache hashes use filtered display text.
- Markdown and Feishu share the same structured Show Notes builder.
- Translation failure must not block the weekly report.
- Invalid, incomplete, or mixed fallback output returns the filtered original.
- Source URLs, timestamps, bullets, and useful resource links should survive
  translation. Missing URLs are appended deterministically.
- Translation diagnostics belong in metadata, not user-facing error dumps.

The supported OpenClaw model-turn form is:

```text
openclaw agent --agent <agent_id> --message <prompt> --json --timeout <seconds>
```

Do not introduce the obsolete `--model ... eval --prompt ...` form. Do not
replace unavailable `openclaw web-search` with an agent call implicitly;
agent calls can consume model quota and require explicit product intent.

## Local Transcription Contract

- The transcription CLI accepts one existing local regular file and rejects
  HTTP/HTTPS input. It must not resolve or download podcast audio.
- It is independent of RSS ingestion, weekly selection, OpenClaw, Feishu, and
  `podcast_screener_cron.sh`.
- Capability checks must not import/load models, download model files, create
  runtime directories, or make network calls.
- Backend adapters stay injectable. Tests use fake Whisper modules, fake
  subprocesses, and temporary directories; they never process real audio.
- Keep stdout machine-readable JSON and use stderr for diagnostics.
- Preserve atomic publication, stable exit codes, and reuse based on source
  hash plus backend/model/language fingerprint.

## Scoring And Selection

- Scores use a 0-100 scale.
- `selection_policy.mode: all_preview` keeps valid episodes as preview and uses
  scores for ordering and suggestions.
- `full_suggestion` is advisory and does not silently change `decision`.
- Keep guest/background evidence separate from broad topic text.
- Missing optional guest enrichment must not fail the weekly report.

## Delivery Contract

- A successful `delivery_meta` prevents duplicate document creation unless
  `--force` is explicit.
- A successful `notification_meta` prevents duplicate notification unless
  `--force` is explicit.
- A failed or incomplete prior attempt must fail closed instead of guessing.
- Notification requires successful delivery metadata with a document ID/URL.
- `--dry-run` must not call APIs or mutate run state.

## Runtime And Scheduling

The default root is derived from the repository. Supported overrides include:

- `PODCAST_PIPELINE_HOME`
- `PIPELINE_DIR` for legacy compatibility
- `PODCAST_PIPELINE_CONFIG_DIR`
- `PODCAST_PIPELINE_OUTPUT_DIR`
- `PODCAST_PIPELINE_STATE_DIR`
- `PODCAST_PIPELINE_LOG_DIR`
- `PODCAST_PIPELINE_PYTHON`
- `PODCAST_PIPELINE_EXTRA_PATH`
- `PODCAST_PIPELINE_PROXY`
- `PODCAST_PIPELINE_ALL_PROXY`

Path helpers resolve paths but do not create directories at import time.
Entrypoints create writable directories immediately before use.

The business window is Sunday 22:00 to the following Sunday 22:00 in
Asia/Shanghai. Scheduler configuration is local operator state and must not be
committed.

## Verification

Primary safe regression command:

```bash
python3 scripts/run_safe_regression.py
```

It runs all `scripts/test_*.py`, shell lock coverage, Python compilation, shell
syntax checks, and Git whitespace checks. It must stay free of real external
calls.

Useful focused checks:

```bash
python3 scripts/test_rss_adapter.py
python3 scripts/test_episode_show_notes_renderer.py
python3 scripts/test_show_notes_translation_orchestrator.py
python3 scripts/test_report_rendering_golden.py
python3 scripts/test_feishu_blocks_renderer.py
python3 scripts/test_delivery_chain.py
python3 scripts/test_repository_hygiene.py
```

Production commands requiring explicit approval:

```bash
python3 scripts/podcast_screener.py
bash scripts/podcast_screener_cron.sh
python3 scripts/deliver_weekly_report_to_feishu.py
python3 scripts/feishu_notify.py
openclaw agent ...
```

The cron diagnostic is safe when explicitly enabled:

```bash
PODCAST_SCREENER_CRON_DRY_RUN=1 bash scripts/podcast_screener_cron.sh
```

## Documentation

Keep public documentation task-oriented:

- `README.md`: installation, common configuration, operation, and boundaries.
- `docs/architecture.md`: component ownership and data flow.
- `docs/configuration.md`: tracked defaults, local overrides, and credentials.
- `docs/operations.md`: safe verification, production operation, and recovery.
- `data_schema.md`: emitted JSON contract.
- `CHANGELOG.md`: user-visible changes.

Do not add private incident logs, weekly operator notes, local filesystem paths,
or long internal conversation transcripts to the public repository.
