# Podcast Pipeline Data Schema

> Current contract: 2026-08-10. The emitted score range is 0-100.

## Run Result

Each immutable run is written to:

```text
outputs/runs/{week_id}/{run_id}/screening_result.json
outputs/runs/{week_id}/{run_id}/screening_report.md
```

Top-level fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `run_id` | string | Local run timestamp identifier. |
| `week_id` | string | Business-week ID such as `2026W28`. |
| `window_start` | string | Inclusive Asia/Shanghai boundary. |
| `window_end` | string | Exclusive Asia/Shanghai boundary. |
| `timezone` | string | Currently `Asia/Shanghai`. |
| `interval_rule` | string | Stored comparison rule. |
| `window_semantics` | string | `last_completed_business_week`. |
| `scan_date` | string | Local calendar date of the run. |
| `total_episodes` | integer | Number of result records. |
| `fetch_errors` | array | Feed IDs that failed fetch/parse. |
| `full` | array | Episodes selected as full in score-based mode. |
| `preview` | array | Preview episodes; production currently uses all-preview. |
| `skip` | array | Invalid, ad-only, short, or score-skipped episodes. |
| `runtime_metadata` | object | Git and non-sensitive runtime config diagnostics. |
| `show_notes_translation_summary` | object | Bounded weekly translation health counts and failed episode labels. |
| `delivery_meta` | object, optional | Idempotent Feishu document status. |
| `notification_meta` | object, optional | Idempotent Feishu group status. |

`runtime_metadata` contains the generating Git commit and only safe Show Notes
translation config keys (`enabled`, `mode`, `agent_id`, `model`). It must not
contain credentials, prompts, translated bodies, or account tokens.

## Episode Record

### Identity And Source

- `podcast_id`, `podcast_name`
- `episode_id`, `episode_title`
- `publish_date`, `publish_at`, `pub_datetime`
- `duration_seconds`: exact parsed RSS duration when available
- `duration_minutes`: floor-divided compatibility duration
- `audio_url`, `language`
- `show_notes_text`: cleaned RSS text retained as the original result field
- `show_notes_source`, `show_notes_text_len`, `show_notes_truncated`
- `rss_description_len`, `rss_content_encoded_len`, `rss_itunes_summary_len`

The renderer filters display-only sponsor/footer blocks. It does not overwrite
`show_notes_text`, and scoring continues to use the result source field.

### Score And Decision

All dimensions are integers from 0 to 100 except `final_score`, which is a
rounded float in the same range:

- `topic_relevance`
- `information_density`
- `novelty`
- `actionability`
- `strategic_value`
- `transcription_value`
- `final_score` and compatibility alias `score`

Decision fields:

- `decision`: `full`, `preview`, or `skip`
- `decision_reason_code`: stable machine-readable all-preview reason
- `decision_reason_zh`: Chinese reason for the actual decision
- `priority`: `high`, `medium`, or `low`
- `full_suggestion`: `yes`, `maybe`, or `no`
- `selection_policy_mode`: currently `all_preview` in production
- `reason_zh`, `uncertainty_zh`
- `summary_3_sentences_cn`, `one_line_summary_cn`, `key_points_cn`
- `topics`, `keywords`, `why_important`, compatibility field `reason`

In `all_preview` mode, score orders episodes but does not silently switch the
collection policy to fixed thresholds. `score_based` thresholds remain an
explicit alternative and require separate calibration.

The all-preview classifier uses explicit `selection_policy` duration limits and
narrow teaser/paywall/cross-podcast rules. Renderers prefer
`decision_reason_zh` for Skip output and fall back to legacy `reason_zh` /
`reason` fields when reading old result JSON.

### Guest Data

- `guest_detection_status`: `confirmed_guest`, `possible_guest`,
  `mentioned_entity`, `ambiguous`, or `no_guest_detected`
- `guest_names`: canonical, de-duplicated names
- `guest_detection_evidence`: structured pattern/source evidence
- `guest_background_zh`: natural Chinese background or the standard fallback
- `guest_background_sources`: quality-rated source records
- `guest_background_confidence`, `guest_background_note`

External guest search/model failure is best-effort and must not fail the run.
Guest cache data lives under ignored local state and is not embedded wholesale
in the result.

### Show Notes Display Metadata

`show_notes_display_metadata` is diagnostics for the shared Markdown/Feishu
display path. It does not duplicate translated text.

Typical fields include:

- `heading`, `translation_status`, `translation_attempted`
- language classification and evidence
- `cache_hit`, cache key/source hash, translation/cache version
- chunk counts and bounded error details
- residual-English and source-completeness diagnostics
- `display_filter` category counts, removal reasons, and filter errors

Translation statuses include `translated`, `cache_hit`, `skipped`, `failed`,
and `partial_failed`. Any failure falls back to filtered original Show Notes.
The translated display heading is:

```text
节目介绍 / Show Notes（中文翻译，原文已保留）
```

The original English remains in `show_notes_text`; it is not duplicated as a
raw report appendix.

### Show Notes Display Snapshot

`show_notes_display_snapshot` is a versioned rendering artifact containing the
final Show Notes heading and ordered display sections produced for Markdown.
Feishu prefers this snapshot for the same run, preventing a second translation
call or divergent output during delivery. It may contain translated display
text, but it never replaces the original `show_notes_text`.

At run level, `show_notes_translation_summary` aggregates `episode_count`,
`eligible_count`, `translated_count`, `partial_count`, `failed_count`,
`skipped_count`, `cache_hit_count`, and `visible_translation_count`, plus a
bounded `failed_episodes` list. Cron and notification surfaces may report these
counts; failures remain non-fatal to report generation and delivery.

### Registry And Delivery

- `registry_status`: first/last seen timestamps, first week, count, previous
  decision, and history count
- `delivery_targets`: optional non-secret folder IDs/URLs resolved for the
  episode

Run-level `delivery_meta` records document ID/URL, folder data, status, and
attempt/delivery timestamps. `notification_meta` records the delivered document
identity, status, and notification timestamp. Both are updated atomically by
their owning store helpers and support idempotent retry/force behavior.

## Configuration Schema

### `config/podcasts.yaml`

Each podcast includes:

- `id`, `name`, `rss_url`
- `feed_type`: `rss`, `apple_jsonld`, `html_jsonld`, or `manual`
- `language`
- `network_mode`: `direct`, `proxy`, or `auto`
- optional registry labels such as priority/source class

### `config/policy.yaml`

Major sections:

- `processing_policy`, `preview_policy`, `full_transcribe_policy`
- `score_policy`, `selection_policy`, `full_suggestion_policy`
- `language_policy`, `ad_detection_policy`, `feishu_policy`, `whisper`
- `show_notes_translation`

The translation section supports `enabled`, `mode`, `target_language`,
`cache_enabled`, `cache_dir`, `model`, `agent_id`, `max_chunk_chars`, and
`timeout_seconds`. Tracked defaults are `enabled: false` and `mode: mock`.
Optional ignored `config/policy.local.yaml` is recursively merged over the
tracked policy for operator-specific settings. OpenClaw account details never
belong in either YAML.

### Local JSON

`config/feishu_config.json` may contain `app_id`, `app_secret`, and
`webhook_url`. `config/feishu_folder_mapping.json` contains folder IDs/URLs.
Both are ignored; committed `.example.json` files contain empty values.
