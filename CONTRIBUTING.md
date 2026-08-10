# Contributing

Thank you for improving `podcast_pipeline`. Changes should preserve the weekly
pipeline's safety boundaries and remain small enough to review.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 scripts/run_safe_regression.py
```

Python 3.9 or newer is supported. Optional transcription dependencies are in
`requirements-transcription.txt` and are not needed for core tests.

## Change Rules

- Add or update hermetic tests for behavior changes.
- Do not call real RSS, Feishu, OpenClaw, MiniMax, or web-search services in CI.
- Never commit `.env`, local Feishu JSON, state, cache, outputs, logs, audio, or
  transcripts.
- Do not change the business-week window, `all_preview` selection policy, or
  launchd schedule as an incidental refactor.
- Keep renderer, cache, and scoring helpers import-safe and free of filesystem
  side effects.
- Update `CHANGELOG.md` and user-facing docs when behavior changes.

## Pull Requests

Describe the user-visible behavior, risk boundaries, tests run, and any manual
validation. A pull request should pass `python3 scripts/run_safe_regression.py`
without credentials or external services.
