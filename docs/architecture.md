# Architecture

## Pipeline

```text
tracked config + optional local policy override
  -> RSS fetch and parse
  -> completed business-week window
  -> dedupe and duration validation
  -> structured scoring and selection
  -> guest detection and optional enrichment
  -> Show Notes display filtering
  -> optional translation and cache
  -> screening_result.json + Markdown
  -> Feishu block rendering
  -> idempotent document delivery
  -> idempotent group notification
```

The weekly pipeline is a set of local scripts rather than a server. Runtime
state is written only when an entrypoint runs; importing parser, renderer,
policy, path, or cache helpers must not create files or call external services.

## Component Boundaries

### Runtime paths and policy

`pipeline_paths.py` resolves the repository, config, output, state, and log
roots. Environment overrides are supported, but path resolution itself is
side-effect free.

`policy_config.py` loads tracked `config/policy.yaml` and recursively merges the
optional ignored `config/policy.local.yaml`. Screener and delivery paths use the
same effective policy contract.

### RSS ingestion

`rss_adapter.py` owns network fetching and RSS/Atom parsing. It preserves the
raw source fields used by downstream logic, including normalized
`show_notes_text`, duration metadata, episode URL, GUID, publication time, and
audio enclosure.

Feed failures are collected per podcast. One unavailable feed does not discard
successful results from the other feeds.

### Business window and scoring

`podcast_screener.py` owns the completed Asia/Shanghai business-week window,
deduplication, scoring, selection, run assembly, and Markdown report creation.

`episode_duration.py` normalizes duration values and distinguishes exact,
estimated, preview-like, suspicious, and missing durations. Selection policy
can reject invalid or too-short episodes without inventing a duration.

Scores use a 0-100 scale. In the public default `all_preview` mode, valid
episodes remain preview items and scores primarily control ordering and
recommendations.

### Guest enrichment

Guest handling is split across modules:

- `guest_source_quality.py`: source/evidence quality rules
- `guest_cache_store.py`: local cache storage
- `guest_search_adapter.py`: optional search adapters and safe fallback
- `guest_background_generator.py`: deterministic and optional model generation
- `guest_background_fetcher.py`: orchestration

Guest failure is non-fatal. The weekly display does not depend on successful
guest enrichment, and optional data remains available in result JSON.

### Show Notes display and translation

`episode_show_notes_renderer.py` builds the shared structured Show Notes
sections consumed by Markdown and Feishu. It filters recognized sponsor,
subscription, social, and footer blocks while retaining body content, resource
links, timestamps, and bullet structure.

Translation is decomposed into:

- language detection
- paragraph-aware chunking
- versioned cache storage
- generic runner orchestration
- OpenClaw subprocess adapter
- quality and URL preservation checks

The translation source and cache hash use filtered display text. A cache miss
can call the configured runner; cache hits avoid model calls. Failed or
incomplete translations fall back to filtered original text without failing the
report.

### Rendering and delivery

`feishu_blocks_renderer.py` is a pure renderer. It receives the same episode
data and translation options as Markdown, so display filtering and translation
headings stay aligned.

`latest_result_store.py` owns latest pointers plus delivery and notification
metadata. Delivery scripts validate this metadata before any external call.

`deliver_weekly_report_to_feishu.py` creates the report document.
`feishu_notify.py` sends the group summary only after a successful document
delivery. Both paths are idempotent and require explicit `--force` to repeat a
successful action.

### Production coordination

`podcast_screener_cron.sh` is the fail-closed coordinator:

1. acquire the run lock
2. validate runtime paths, Python, proxy, and translation mode
3. run the screener
4. deliver the Feishu document
5. send the notification

A failed step stops the chain. `PODCAST_SCREENER_CRON_DRY_RUN=1` prints
diagnostics without running the three production Python entrypoints.

## Runtime Data

Each run is stored under:

```text
outputs/runs/{week_id}/{run_id}/
  screening_result.json
  screening_report.md
```

Run directories are immutable history. Latest pointers identify the current
complete run. Translation cache, guest cache, state, logs, downloaded audio,
and transcripts remain local and ignored by Git.

See [data_schema.md](../data_schema.md) for the emitted JSON contract.

## Failure Model

- Feed failure: record the podcast error and continue other feeds.
- Optional guest lookup/model failure: keep the episode and omit enrichment.
- Translation failure: render filtered original Show Notes.
- Screener failure: do not start delivery.
- Delivery failure: do not send notification.
- Existing incomplete delivery metadata: stop for manual inspection.
- Duplicate successful delivery/notification: skip unless `--force` is used.

This separation keeps optional model and delivery integrations from corrupting
the local weekly result.
