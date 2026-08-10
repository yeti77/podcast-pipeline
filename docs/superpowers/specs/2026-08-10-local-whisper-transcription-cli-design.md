# Local Whisper Transcription CLI Design

## Status

Approved product boundary for the optional transcription layer. This design is
limited to converting an existing local audio file into standard transcript
artifacts. It does not automate episode selection, audio download, Feishu
message handling, or agent orchestration.

## Goal

Provide one stable, agent-neutral command that OpenClaw, Codex, another local
agent, or a human operator can call after an audio file already exists on the
machine:

```bash
python3 scripts/podcast_transcriber.py \
  --audio /absolute/path/to/episode.mp3 \
  --output-dir /absolute/path/to/transcripts/episode
```

The command selects a supported local Whisper backend, transcribes the audio,
and emits predictable TXT, SRT, VTT, and JSON metadata files. The weekly RSS,
report, translation, and Feishu delivery pipeline remains independent of this
optional command.

## User Story

1. A user reads the weekly report and chooses an episode.
2. The user or their preferred agent obtains the audio file by a method they
   control.
3. The user or agent invokes the local transcription CLI with that file.
4. The CLI reports a machine-readable status and writes standard artifacts.
5. The user or agent reads `transcript.txt` or uses the subtitle files for
   later summarization and note taking.

The repository supplies step 3 and step 4. It documents the handoff around
them, but does not own step 2 or any agent-specific chat workflow.

## Scope

### Included

- Validate a local audio input path.
- Check local `ffmpeg`/`ffprobe` and Whisper Python dependencies.
- Support MLX Whisper on compatible Apple Silicon systems.
- Support OpenAI Whisper as a portable fallback.
- Select a backend automatically or accept an explicit backend.
- Accept language and model overrides.
- Write normalized TXT, SRT, VTT, and metadata artifacts.
- Reuse a matching successful result unless `--force` is supplied.
- Return stable process exit codes and machine-readable stdout.
- Provide hermetic tests using fake backends and temporary directories.
- Document installation and a generic agent invocation contract.

### Excluded

- Resolving or downloading episode audio.
- Reading an RSS feed to find an enclosure.
- Listening for Feishu messages or creating Feishu documents.
- Calling OpenClaw, Codex, MiniMax, or any remote model.
- Automatically selecting episodes from a weekly report.
- Running from `podcast_screener_cron.sh` or another scheduler.
- Summarizing, translating, or otherwise rewriting the transcript.
- Uploading audio or transcript artifacts.

The existing `audio_resolver.py`, selection queue scripts, and Feishu selection
document code are not dependencies of this CLI and are not promoted as part of
the supported transcription path in this change.

## Command Interface

### Transcription

```text
python3 scripts/podcast_transcriber.py
  --audio PATH
  --output-dir PATH
  [--language auto|zh|en|LANGUAGE_CODE]
  [--backend auto|mlx|openai]
  [--model MODEL_NAME]
  [--force]
```

- `--audio` must name an existing local regular file. HTTP and HTTPS values are
  rejected so the command cannot silently become a downloader.
- `--output-dir` may be new; the CLI creates it after input validation.
- `--language` defaults to `auto`. Other values are passed to the selected
  backend as explicit language codes.
- `--backend` defaults to `auto`.
- `--model` overrides the model selected from effective policy.
- `--force` ignores a matching successful prior result and retranscribes.

### Environment check

```bash
python3 scripts/podcast_transcriber.py --check
```

Check mode performs no transcription, network access, model download, or file
creation. It reports:

- platform and machine architecture
- `ffmpeg` and `ffprobe` availability
- `mlx_whisper` import availability
- `whisper` import availability
- the backend that `auto` would select
- the configured primary and fallback models

Check mode exits successfully when at least one supported backend and the
required media tools are available.

## Backend Selection

`auto` uses this order:

1. On Apple Silicon, use MLX Whisper when `mlx_whisper` imports successfully.
2. Otherwise use OpenAI Whisper when `whisper` imports successfully.
3. If neither backend is available, return an environment error.

When an automatically selected MLX transcription fails at runtime and OpenAI
Whisper is installed, the command may attempt the configured OpenAI fallback.
An explicitly selected backend does not silently switch to another backend.

CLI model overrides take precedence over policy. Without an override:

- MLX uses `whisper.whisper_model`.
- OpenAI uses `whisper.whisper_fallback_model` when reached as fallback.
- Explicit OpenAI selection uses the configured OpenAI fallback model.

The implementation keeps backend adapters separate from CLI parsing and
artifact writing so tests can inject fake adapters without importing real
models.

## Output Contract

The output directory contains:

```text
transcript.txt
transcript.srt
transcript.vtt
transcription_meta.json
```

Successful metadata has this shape:

```json
{
  "schema_version": 1,
  "status": "success",
  "source_audio": "/absolute/path/to/episode.mp3",
  "source_sha256": "...",
  "backend": "mlx",
  "model": "large-v3-turbo",
  "language_requested": "auto",
  "language_detected": "en",
  "audio_duration_seconds": 3600.0,
  "elapsed_seconds": 42.5,
  "created_at": "2026-08-10T12:00:00+08:00",
  "outputs": {
    "txt": "/absolute/path/to/transcript.txt",
    "srt": "/absolute/path/to/transcript.srt",
    "vtt": "/absolute/path/to/transcript.vtt"
  }
}
```

The CLI prints one JSON summary to stdout. Human-readable progress and backend
diagnostics go to stderr so an agent can parse stdout reliably.

Final artifacts are written atomically. A failed run must not replace a prior
successful transcript with partial output.

## Reuse And Force Behavior

The metadata fingerprint consists of:

- source audio SHA-256
- selected backend
- selected model
- requested language
- metadata schema version

When all final artifacts are present and non-empty and the fingerprint matches,
the CLI returns status `reused` without loading a model. `--force` bypasses this
check. A changed audio file or changed transcription option naturally produces
a new run.

## Exit Codes

| Code | Meaning |
| --- | --- |
| `0` | check passed, transcription succeeded, or matching result reused |
| `2` | invalid arguments or invalid local input path |
| `3` | missing media tool or Whisper backend dependency |
| `4` | transcription backend failed |
| `5` | output directory or artifact write failed |

The stdout JSON and stderr diagnostic identify the same failure category.

## Agent Contract

The supported integration is deliberately plain:

1. The agent obtains a local audio file according to the user's instructions.
2. The agent runs `--check` before the first transcription on a machine.
3. The agent invokes the CLI with one explicit input file and one explicit
   output directory.
4. The agent parses stdout JSON or `transcription_meta.json`.
5. The agent reports the transcript path and does not infer success from file
   names alone.

The documentation includes a reusable prompt that forbids processing unrelated
files, changing project configuration, or uploading audio. No OpenClaw-specific
skill, Codex automation, or Feishu bot is required for this contract.

## Installation Documentation

`docs/transcription.md` will contain:

- shared prerequisites: Python environment, `ffmpeg`, and disk-space warning
- Apple Silicon path using MLX Whisper
- portable path using OpenAI Whisper
- installation through `requirements-transcription.txt`
- `--check` and one-file transcription examples
- first-run model download warning
- expected output layout
- common errors and backend selection guidance
- local privacy, storage, and copyright boundaries
- the generic agent invocation prompt

The README will describe transcription as an optional second layer and link to
the dedicated guide. The base Quick Start remains free of heavy Whisper and
Torch dependencies.

## Error Handling

- Missing input: reject before creating the output directory.
- URL supplied as input: reject and explain that only local files are accepted.
- Missing `ffmpeg`/`ffprobe`: return environment error.
- Missing selected backend: return environment error with the exact optional
  dependency to install.
- Backend exception: preserve previous successful artifacts and return a
  transcription error.
- Empty transcript: treat as transcription failure unless the backend provides
  non-empty timed segments that can reconstruct text.
- Output failure: preserve any previous successful artifact set and return an
  output error.
- Existing incomplete output: do not report reuse; run again unless validation
  or environment checks fail.

## Testing Strategy

Create hermetic tests for:

- local input validation and URL rejection
- check-mode capability reporting
- automatic backend selection on simulated platforms
- explicit backend selection without silent fallback
- automatic MLX-to-OpenAI runtime fallback
- language and model option precedence
- normalized TXT, SRT, and VTT rendering from fake segments
- metadata schema and stdout JSON
- matching result reuse without backend invocation
- `--force` retranscription
- changed source hash invalidation
- atomic write behavior on backend and output failures
- stable exit-code mapping
- import safety and absence of RSS, OpenClaw, Feishu, or network calls

Tests use temporary files, fake backend functions, and fake media-tool probes.
CI does not install `requirements-transcription.txt`, download a Whisper model,
or process real audio.

## Acceptance Criteria

- A user with a local audio file can obtain TXT, SRT, VTT, and metadata through
  one documented command.
- An agent can determine success, reuse, or failure without parsing prose.
- `--check` has no network or filesystem side effects.
- The command never downloads audio or calls an external model or delivery API.
- Core weekly screening and safe regression remain usable without Whisper
  dependencies.
- Existing production scheduling, RSS, scoring, translation, and Feishu paths
  are unchanged.
- Public documentation does not claim that Feishu-to-agent download and
  transcription orchestration is included.

## Rollout

1. Implement and test the local-file CLI with fake backends.
2. Add installation and agent-contract documentation.
3. Run the complete hermetic safe regression.
4. Perform one explicitly authorized local short-audio dry-run outside CI.
5. Publish the feature only after the dry-run confirms artifacts and metadata.

The dry-run is evidence for the optional local transcription layer; it does not
change the weekly production pipeline or enable automatic transcription.
