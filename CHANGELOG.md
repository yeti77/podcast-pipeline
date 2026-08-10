# Changelog

This project follows a lightweight Keep a Changelog structure. It has not yet
published a numbered release.

## Unreleased

### Added

- Runtime commit/config metadata and per-episode Show Notes translation
  diagnostics.
- Structured Show Notes block classification and display-filter diagnostics.
- Portable path resolution, hermetic delivery fixtures, repository hygiene
  checks, and GitHub CI.
- Secret-safe Feishu configuration examples and environment credential support.

### Changed

- Cron delivery is fail-closed and its runtime root/PATH/proxy are portable.
- Short explicit-English Show Notes can enter translation safely.
- Guest background title/organization evidence is normalized and guarded.
- Information density and English strategic/action scoring signals are
  calibrated while production remains in `all_preview` mode.

### Fixed

- OpenClaw CLI compatibility, filtered translation cache keys, source URL
  preservation, and sponsor/footer leakage in translated display text.
- Residual guest candidate fragments and several real-world guest identity
  variants.

### Security

- Runtime state, cache, outputs, logs, local credentials, and `.env` are kept
  outside version control.
