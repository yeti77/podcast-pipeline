# Operations

## Safe Verification

Run the hermetic suite after setup and before every release:

```bash
python3 scripts/run_safe_regression.py
```

It runs unit/integration fixtures, Python compilation, shell syntax, cron lock
coverage, repository hygiene, and Git whitespace checks. It does not fetch RSS,
run OpenClaw, call Feishu, or read production output/cache.

## Preflight

Inspect the cron runtime without starting production steps:

```bash
PODCAST_SCREENER_CRON_DRY_RUN=1 \
PODCAST_PIPELINE_PROXY=off \
bash scripts/podcast_screener_cron.sh
```

Confirm the diagnostic output shows the intended:

- pipeline root
- config/output/state/log directories
- Python executable
- PATH and OpenClaw availability
- proxy mode
- effective translation enabled/mode/agent values
- three production commands in the expected order

Dry-run must stop before executing screener, delivery, or notification.

## Production Chain

The supported coordinator is:

```bash
bash scripts/podcast_screener_cron.sh
```

It acquires a lock and executes:

1. `python3 scripts/podcast_screener.py`
2. `python3 scripts/deliver_weekly_report_to_feishu.py`
3. `python3 scripts/feishu_notify.py`

The chain is fail-closed. A screener failure prevents delivery; a delivery
failure prevents notification. Do not invoke later stages manually until the
failed run and its metadata have been inspected.

## Scheduling

The default business window is Sunday 22:00 to the following Sunday 22:00 in
Asia/Shanghai. Trigger the wrapper after the window closes.

On macOS, configure a local LaunchAgent whose `ProgramArguments` call the
repository's `scripts/podcast_screener_cron.sh`. Keep the plist outside the
repository because paths and environment differ per machine.

The scheduler environment is usually smaller than an interactive shell. Set
these explicitly when needed:

- `PODCAST_PIPELINE_HOME`
- `PODCAST_PIPELINE_PYTHON`
- `PODCAST_PIPELINE_EXTRA_PATH`
- `PODCAST_PIPELINE_PROXY`
- OpenClaw's required PATH/account environment

Re-run cron dry-run from a launchd-like environment after changing paths,
Python, OpenClaw, proxy, or local policy.

## Local Report Generation

The screener alone writes local run artifacts and performs no Feishu delivery:

```bash
python3 scripts/podcast_screener.py
```

This command performs real RSS requests and may run OpenClaw when effective
policy enables it. Use `--run-date YYYY-MM-DD` only for deliberate historical
window verification.

Each successful run creates:

```text
outputs/runs/{week_id}/{run_id}/screening_result.json
outputs/runs/{week_id}/{run_id}/screening_report.md
```

Do not edit a completed run in place. Use a manual-rerun directory for offline
preview work.

## Feishu Dry-Run And Delivery

With a valid latest result/report, validate rendering without API calls:

```bash
python3 scripts/deliver_weekly_report_to_feishu.py --dry-run
python3 scripts/feishu_notify.py --dry-run
```

Real delivery requires credentials, folder mapping, and application permission
to create/modify documents in the selected folder.

Successful delivery and notification metadata make retries idempotent. Use
`--force` only after confirming that a deliberate duplicate document or message
is acceptable.

## Translation Operations

Translation is optional and public policy defaults to disabled/mock. When a
local override enables OpenClaw:

1. display filtering runs before language detection and cache lookup
2. eligible English text is split into bounded chunks
3. cache hits avoid runner calls
4. cache misses call the configured OpenClaw agent
5. output is checked for completeness and source URL preservation
6. invalid output falls back to filtered original Show Notes

Operational checks should use result metadata instead of searching only for a
heading. Review:

- eligible/translated/fallback episode counts
- cache hit/miss counts
- runner failures and partial failures
- filtered block category counts
- source and translated length ratios
- missing URL diagnostics

Never reuse a manual preview cache as production cache without confirming the
same filtered source hash and translation version.

## Troubleshooting

### No episodes or feed errors

1. Validate the affected RSS URL independently.
2. Confirm HTTP status, XML channel/feed, item count, and enclosure.
3. Compare episode publication time with the completed business window.
4. Inspect `fetch_errors` in `screening_result.json`.

Do not replace the whole weekly run just because one feed fails.

### Unexpected short duration

Inspect duration metadata and source fields. The system distinguishes exact,
estimated, preview-like, suspicious, and missing values. Do not infer a full
episode duration from a short trailer or malformed RSS field.

### English Show Notes remain untranslated

1. Confirm effective policy is enabled and mode is `openclaw`.
2. Confirm `agent_id` is present and OpenClaw is on scheduler PATH.
3. Inspect language-detection status and eligibility.
4. Inspect cache and translation diagnostics for fallback reasons.
5. Confirm the source was not intentionally classified as mixed/unknown.
6. Check whether output failed completeness or URL-preservation validation.

Fallback to `Show Notes (full)` is a safe behavior, not a delivery failure.

### OpenClaw works in Terminal but not scheduler

The likely cause is PATH or account environment differences. Compare cron
dry-run diagnostics with the interactive shell. Do not hard-code a Node version
or a developer home directory into production scripts.

### Feishu document not created

1. Run delivery `--dry-run`.
2. Check app ID/secret source and folder mapping.
3. Confirm application folder permissions.
4. Inspect existing `delivery_meta` for failed/incomplete status.
5. Use `--force` only after resolving the prior state.

### Notification not sent

Notification requires successful delivery metadata. Confirm document ID/URL,
webhook configuration, and `notification_meta`. The notifier must not guess a
document URL or send before delivery succeeds.

### Locked run

The wrapper prevents overlapping weekly runs. Confirm no process is active
before removing a stale local lock. Do not disable locking to work around a
slow OpenClaw or network call.

## Recovery Principles

- Preserve completed run directories as immutable evidence.
- Prefer a new explicit rerun over editing production JSON manually.
- Keep caches disposable; result JSON remains the source of run diagnostics.
- Rotate credentials immediately if they appear in a commit, log, issue, or
  shared output.
- Stop downstream delivery after any uncertain screener state.

## Publication Boundary

Before publishing or accepting a contribution:

1. run the complete safe regression
2. inspect tracked files and staged diff for credentials and absolute paths
3. ensure local policy, Feishu JSON, outputs, cache, logs, and state are ignored
4. confirm CI runs only the hermetic regression entrypoint
5. review dependency and GitHub Action versions

Public examples must use empty or obviously fake values.
