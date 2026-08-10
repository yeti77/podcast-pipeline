# Security Policy

## Reporting A Vulnerability

Please use GitHub's private vulnerability reporting feature when it is enabled
for this repository. Do not open a public issue containing credentials, Feishu
URLs, OpenClaw account data, private podcast feeds, or runtime output samples.

Include the affected commit, reproduction steps, expected impact, and whether
the issue can trigger external calls or expose local files. Maintainers should
acknowledge a report before discussing disclosure timing.

## Secrets And Local Data

Real values belong in environment variables or ignored local files:

- `config/feishu_config.json`
- `config/feishu_folder_mapping.json`
- `.env`
- `state/`, `cache/`, `outputs/`, `logs/`, transcripts, and downloaded audio
- `~/.openclaw/openclaw.json`

The example files contain empty values. Rotate any credential immediately if it
is committed or pasted into an issue, even if the commit is later removed.

## Supported Version

Security fixes target the current default branch. Historical commits and local
manual-run artifacts are not supported release channels.
