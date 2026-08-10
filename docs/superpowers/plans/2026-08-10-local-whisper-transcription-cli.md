# Local Whisper Transcription CLI Implementation Plan

**Goal:** Turn an existing local audio file into stable TXT, SRT, VTT, and JSON metadata through one agent-neutral command without downloading audio or changing the weekly pipeline.

**Architecture:** Refactor `podcast_transcriber.py` into an import-safe library plus CLI. Keep input validation, capability checks, backend adapters, transcript formatting, reuse, and artifact publication as separate functions so hermetic tests can inject fake media probes and fake Whisper modules. The CLI accepts only local files, prints one JSON result to stdout, sends diagnostics to stderr, and never participates in cron, RSS, Feishu, or OpenClaw paths.

**Tech Stack:** Python 3.9+, `argparse`, `pathlib`, `hashlib`, `json`, `subprocess`, `tempfile`, `unittest`, optional `mlx-whisper`, optional `openai-whisper`, and `ffmpeg`/`ffprobe`.

---

## File Map

- Modify `scripts/podcast_transcriber.py`: complete local-file transcription library and CLI while preserving `CONFIG_DIR`, `_WHISPER_CFG`, and `load_whisper_config()` compatibility.
- Create `scripts/test_podcast_transcriber.py`: hermetic unit and CLI-contract coverage with fake backends, fake imports, and temporary files.
- Create `docs/transcription.md`: optional dependency setup, command usage, agent contract, privacy, and troubleshooting.
- Modify `README.md`: describe the optional second layer and link to the dedicated guide.
- Modify `docs/architecture.md`: add the local-only transcription boundary outside the weekly production chain.
- Modify `AGENTS.md`: document the transcription module's no-download/no-network boundary for contributors.
- Modify `CHANGELOG.md`: record the new optional local transcription CLI under an Unreleased section.
- No change to `requirements.txt`, `requirements-transcription.txt`, cron, RSS, Feishu, OpenClaw, selection, or scoring modules.

## Task 1: Local Input And Request Contract

**Files:**
- Create: `scripts/test_podcast_transcriber.py`
- Modify: `scripts/podcast_transcriber.py`

- [ ] **Step 1: Write failing local-input and request tests**

  Add imports for `json`, `Path`, `tempfile`, `unittest`, and `mock`, then import `podcast_transcriber as transcriber`. Add `TestTranscriptionRequest` with these concrete cases:

  ```python
  def test_validate_local_audio_path_accepts_existing_regular_file(self):
      with tempfile.TemporaryDirectory() as tmp:
          audio = Path(tmp) / "episode.mp3"
          audio.write_bytes(b"fixture-audio")
          self.assertEqual(transcriber.validate_local_audio_path(audio), audio.resolve())

  def test_validate_local_audio_path_rejects_http_and_https(self):
      for value in ("http://example.test/a.mp3", "https://example.test/a.mp3"):
          with self.subTest(value=value):
              with self.assertRaisesRegex(transcriber.CliInputError, "local audio file"):
                  transcriber.validate_local_audio_path(value)

  def test_validate_local_audio_path_rejects_missing_and_directory_paths(self):
      with tempfile.TemporaryDirectory() as tmp:
          with self.assertRaises(transcriber.CliInputError):
              transcriber.validate_local_audio_path(Path(tmp) / "missing.mp3")
          with self.assertRaises(transcriber.CliInputError):
              transcriber.validate_local_audio_path(tmp)

  def test_source_sha256_changes_when_audio_changes(self):
      with tempfile.TemporaryDirectory() as tmp:
          audio = Path(tmp) / "episode.mp3"
          audio.write_bytes(b"version-one")
          first = transcriber.sha256_file(audio)
          audio.write_bytes(b"version-two")
          self.assertNotEqual(first, transcriber.sha256_file(audio))
  ```

  Add a request-precedence test that constructs `TranscriptionRequest` through `build_transcription_request()` and asserts CLI `language`, `backend`, and `model` values override the supplied policy mapping.

- [ ] **Step 2: Run the new test to verify RED**

  Run:

  ```bash
  python3 scripts/test_podcast_transcriber.py
  ```

  Confirm failure is caused by missing `CliInputError`, `validate_local_audio_path`, `sha256_file`, `TranscriptionRequest`, or `build_transcription_request`, not by an import error in unrelated modules.

- [ ] **Step 3: Implement the minimal request contract**

  Replace unused imports in `podcast_transcriber.py` and add:

  ```python
  TRANSCRIPTION_METADATA_SCHEMA_VERSION = 1

  class TranscriptionCliError(RuntimeError):
      exit_code = 4
      status = "transcription_error"

  class CliInputError(TranscriptionCliError):
      exit_code = 2
      status = "input_error"

  class EnvironmentCheckError(TranscriptionCliError):
      exit_code = 3
      status = "environment_error"

  class OutputWriteError(TranscriptionCliError):
      exit_code = 5
      status = "output_error"
  ```

  Add a frozen `TranscriptionRequest` dataclass with `audio_path`, `output_dir`, `language`, `backend`, `model`, and `force`. Implement:

  - `validate_local_audio_path(value)` using `Path.expanduser().resolve()`, explicit HTTP/HTTPS rejection, `is_file()`, and no output-directory creation.
  - `sha256_file(path)` using 1 MiB reads.
  - `build_transcription_request(audio, output_dir, language, backend, model, force, policy)` with allowed backends `auto`, `mlx`, and `openai`; CLI values take precedence over the `whisper` policy mapping.
  - Keep `load_whisper_config()` and its global cache contract unchanged for `test_policy_config.py`.

- [ ] **Step 4: Run focused tests to verify GREEN**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  python3 scripts/test_policy_config.py
  python3 scripts/test_pipeline_paths.py
  ```

- [ ] **Step 5: Commit the request contract**

  ```bash
  git add scripts/podcast_transcriber.py scripts/test_podcast_transcriber.py
  git commit -m "feat: define local transcription request contract"
  ```

## Task 2: Side-Effect-Free Capability Check And Backend Selection

**Files:**
- Modify: `scripts/test_podcast_transcriber.py`
- Modify: `scripts/podcast_transcriber.py`

- [ ] **Step 1: Write failing capability and selection tests**

  Add `TestTranscriptionCapabilities` covering:

  - `probe_transcription_capabilities()` returns booleans for `ffmpeg`, `ffprobe`, `mlx_whisper`, and `whisper` when supplied fake `which` and fake `find_spec` functions.
  - Apple Silicon plus MLX available selects `mlx`.
  - Intel/Linux or missing MLX plus OpenAI Whisper available selects `openai`.
  - No available backend raises `EnvironmentCheckError`.
  - Explicit `mlx` with MLX missing raises instead of selecting OpenAI.
  - Explicit `openai` with OpenAI Whisper missing raises instead of selecting MLX.
  - Missing `ffmpeg` or `ffprobe` causes `validate_transcription_environment()` to raise.
  - Check mode functions do not create a supplied nonexistent path and do not import either Whisper runtime module.

  Use concrete fake capability dictionaries such as:

  ```python
  capabilities = {
      "ffmpeg": True,
      "ffprobe": True,
      "mlx_whisper": True,
      "whisper": True,
      "platform_system": "Darwin",
      "platform_machine": "arm64",
  }
  self.assertEqual(transcriber.select_backend("auto", capabilities), "mlx")
  ```

- [ ] **Step 2: Run the focused test to verify RED**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  ```

- [ ] **Step 3: Implement capability probing and deterministic selection**

  Add:

  - `probe_transcription_capabilities(which=shutil.which, find_spec=importlib.util.find_spec, system=platform.system, machine=platform.machine)` without importing Whisper modules.
  - `select_backend(requested_backend, capabilities)` implementing the approved auto order.
  - `validate_transcription_environment(capabilities, selected_backend)` requiring both media tools and the selected backend package.
  - `build_check_result(policy, capabilities)` selecting the configured/automatic backend, calling `validate_transcription_environment()`, and returning a JSON-serializable mapping with `status: "check_ok"`, platform, media tools, backends, selected backend, and configured model names.

  Do not create directories, load models, or call subprocesses in these helpers.

- [ ] **Step 4: Run focused tests to verify GREEN**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  python3 scripts/test_pipeline_paths.py
  ```

- [ ] **Step 5: Commit capability checking**

  ```bash
  git add scripts/podcast_transcriber.py scripts/test_podcast_transcriber.py
  git commit -m "feat: check local Whisper capabilities"
  ```

## Task 3: Whisper Backend Adapters And Transcript Formatting

**Files:**
- Modify: `scripts/test_podcast_transcriber.py`
- Modify: `scripts/podcast_transcriber.py`

- [ ] **Step 1: Write failing formatter tests**

  Add tests for the exact fake segments:

  ```python
  segments = [
      {"start": 0.0, "end": 1.25, "text": " First line. "},
      {"start": 61.5, "end": 63.0, "text": "Second line."},
  ]
  ```

  Assert `segments_to_srt(segments)` contains:

  ```text
  1
  00:00:00,000 --> 00:00:01,250
  First line.
  ```

  Assert `segments_to_vtt(segments)` starts with `WEBVTT` and contains `00:01:01.500 --> 00:01:03.000`. Assert empty or malformed segments are ignored without creating negative timestamps.

- [ ] **Step 2: Write failing fake-backend adapter tests**

  Create fake MLX and OpenAI modules in the test file:

  ```python
  class FakeMlxWhisper:
      def __init__(self, result=None, error=None):
          self.result = result
          self.error = error
          self.calls = []

      def transcribe(self, **kwargs):
          self.calls.append(kwargs)
          if self.error:
              raise self.error
          return self.result
  ```

  Add an equivalent fake OpenAI module with `load_model()` returning a fake model whose `transcribe()` records the audio path and language. Assert:

  - MLX receives `audio`, `path_or_hf_repo`, `language`, and `verbose=False`.
  - OpenAI receives the requested model and calls `transcribe(str(audio_path), language=lang_code)`.
  - Both adapters normalize to `{"text", "segments", "language"}`.
  - Empty normalized text and empty usable segments raise `TranscriptionCliError`.
  - `language="auto"` becomes `None` for each backend call.

- [ ] **Step 3: Run tests to verify RED**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  ```

- [ ] **Step 4: Implement timestamp formatting and adapters**

  Add pure helpers:

  - `format_timestamp(seconds, decimal_marker)` clamping negative values to zero.
  - `segments_to_srt(segments)`.
  - `segments_to_vtt(segments)`.
  - `normalize_backend_result(result)`.

  Add adapters:

  - `run_mlx_backend(request, mlx_module=None)`; import `mlx_whisper` only when no module is injected and convert `large-v3-turbo` to `mlx-community/whisper-large-v3-turbo` unless the configured model already contains `/`.
  - `run_openai_backend(request, whisper_module=None)`; import `whisper` only when no module is injected, load on CPU, and transcribe the local path.
  - `run_selected_backend(request, selected_backend, mlx_runner=run_mlx_backend, openai_runner=run_openai_backend, openai_available=False)`; fallback from automatically selected MLX to OpenAI only when the original request backend is `auto` and OpenAI is available.

  Return the actual backend/model with the normalized result so metadata records fallback accurately.

- [ ] **Step 5: Run focused tests to verify GREEN**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  ```

- [ ] **Step 6: Commit adapters and formatting**

  ```bash
  git add scripts/podcast_transcriber.py scripts/test_podcast_transcriber.py
  git commit -m "feat: add local Whisper backend adapters"
  ```

## Task 4: Duration Probe, Reuse Fingerprint, And Atomic Artifact Publication

**Files:**
- Modify: `scripts/test_podcast_transcriber.py`
- Modify: `scripts/podcast_transcriber.py`

- [ ] **Step 1: Write failing duration and metadata tests**

  Add a fake subprocess result with stdout `"123.456\n"` and assert `probe_audio_duration()` runs an argument-list `ffprobe` command and returns `123.456`. Assert nonzero return code or invalid stdout returns `None` without failing transcription.

  Add tests asserting `build_transcription_metadata()` includes schema version, source absolute path/hash, actual backend/model, requested and detected language, duration, elapsed time, timestamp, and absolute output paths.

- [ ] **Step 2: Write failing reuse tests**

  In a temporary output directory, write non-empty `transcript.txt`, `transcript.srt`, `transcript.vtt`, and matching `transcription_meta.json`. Assert:

  - `find_reusable_result()` returns metadata when source hash, backend, model, language, and schema match.
  - A changed source hash returns `None`.
  - A changed backend/model/language returns `None`.
  - A missing or empty artifact returns `None`.
  - `force=True` bypasses reuse in orchestration and invokes the fake backend.

- [ ] **Step 3: Write failing atomic-publication tests**

  Add one success test that publishes all four named artifacts. Add one failure test with an existing successful output set and an injected writer that raises before publication; assert every old artifact remains byte-for-byte unchanged.

- [ ] **Step 4: Run tests to verify RED**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  ```

- [ ] **Step 5: Implement duration, fingerprint, reuse, and publication**

  Add:

  - `probe_audio_duration(audio_path, run_subprocess=subprocess.run)` using `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1`.
  - `build_reuse_fingerprint(source_sha256, backend, model, language)` containing schema version, source SHA-256, selected backend, selected model, and requested language.
  - `find_reusable_result(output_dir, fingerprint)` validating metadata and all three non-empty transcript artifacts.
  - `build_transcription_metadata(request, source_sha256, backend, model, normalized_result, duration_seconds, elapsed_seconds, created_at)`.
  - `publish_artifacts_atomically(output_dir, text, srt, vtt, metadata, replace=os.replace)`.

  Publication creates the output directory only after input and environment validation, then writes the four managed artifacts into a temporary staging directory beside it. Back up only existing managed files named `transcript.txt`, `transcript.srt`, `transcript.vtt`, and `transcription_meta.json`; replace them from staging with `os.replace`; and restore all backups if any replacement fails. Preserve unrelated files already present in the output directory. Remove backups and staging only after the complete managed set succeeds. Reject an output path that exists as a non-directory.

- [ ] **Step 6: Implement top-level transcription orchestration**

  Add `transcribe_local_audio(request, capabilities=None, backend_runner=run_selected_backend, duration_probe=probe_audio_duration, now=None)`:

  1. validate environment and select backend/model
  2. hash the source
  3. return `status: "reused"` before model load when valid and not forced
  4. run the backend and format artifacts
  5. reject empty text
  6. publish staged artifacts
  7. return `status: "success"` plus metadata

  Send no network requests and do not import RSS, Feishu, OpenClaw, or selection modules.

- [ ] **Step 7: Run focused tests to verify GREEN**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  python3 scripts/test_policy_config.py
  python3 scripts/test_pipeline_paths.py
  ```

- [ ] **Step 8: Commit orchestration and artifacts**

  ```bash
  git add scripts/podcast_transcriber.py scripts/test_podcast_transcriber.py
  git commit -m "feat: publish reusable transcription artifacts"
  ```

## Task 5: Agent-Friendly CLI And Exit Codes

**Files:**
- Modify: `scripts/test_podcast_transcriber.py`
- Modify: `scripts/podcast_transcriber.py`

- [ ] **Step 1: Write failing parser and check-mode tests**

  Add tests that call `main(argv, stdout=stdout, stderr=stderr)` in process with `io.StringIO` streams and patched helpers:

  - `main(["--check"])` returns `0`, emits one JSON object with `status == "check_ok"`, and never calls transcription.
  - Missing `--audio` or `--output-dir` outside check mode returns `2` with `status == "input_error"`.
  - A valid local request returns `0` and emits `status == "success"`.
  - A matching prior result emits `status == "reused"` and returns `0`.
  - `CliInputError`, `EnvironmentCheckError`, generic `TranscriptionCliError`, and `OutputWriteError` map to `2`, `3`, `4`, and `5`.
  - stdout contains only parseable JSON; diagnostics are written to stderr.

- [ ] **Step 2: Write a subprocess import-safety test**

  Run `python3 -c "import podcast_transcriber"` with `PODCAST_PIPELINE_HOME` pointing to a nonexistent temporary directory and assert the directory is still absent. Retain the existing `test_pipeline_paths.py` import test.

- [ ] **Step 3: Run tests to verify RED**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  ```

- [ ] **Step 4: Implement parser and main**

  Add `build_argument_parser()` with `--check`, `--audio`, `--output-dir`, `--language`, `--backend`, `--model`, and `--force`. Keep transcription-only fields optional at the `argparse` layer and validate them in `build_transcription_request_from_args()` so invalid requests return the documented JSON error instead of an uncaught `SystemExit`. Add:

  ```python
  def main(argv=None, *, stdout=None, stderr=None) -> int:
      stdout = stdout or sys.stdout
      stderr = stderr or sys.stderr
      try:
          args = build_argument_parser().parse_args(argv)
          policy = load_whisper_config()
          capabilities = probe_transcription_capabilities()
          if args.check:
              result = build_check_result(policy, capabilities)
          else:
              request = build_transcription_request_from_args(args, policy)
              result = transcribe_local_audio(request, capabilities=capabilities)
          print(json.dumps(result, ensure_ascii=False), file=stdout)
          return 0
      except TranscriptionCliError as exc:
          result = {"status": exc.status, "error": str(exc), "exit_code": exc.exit_code}
          print(json.dumps(result, ensure_ascii=False), file=stdout)
          print(str(exc), file=stderr)
          return exc.exit_code
  ```

  Parse conditionally required arguments after `argparse` so check mode needs no audio. Load effective Whisper policy only after parsing. Print `json.dumps(result, ensure_ascii=False)` once to stdout. Catch `TranscriptionCliError`, print a JSON error with `status`, `error`, and `exit_code`, write the diagnostic to stderr, and return the defined code. End with `raise SystemExit(main())`.

- [ ] **Step 5: Run CLI and compatibility tests to verify GREEN**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  python3 scripts/test_policy_config.py
  python3 scripts/test_pipeline_paths.py
  python3 -m py_compile scripts/podcast_transcriber.py
  python3 -m py_compile scripts/test_podcast_transcriber.py
  ```

- [ ] **Step 6: Commit the CLI**

  ```bash
  git add scripts/podcast_transcriber.py scripts/test_podcast_transcriber.py
  git commit -m "feat: expose local Whisper transcription CLI"
  ```

## Task 6: User And Agent Documentation

**Files:**
- Create: `docs/transcription.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `AGENTS.md`
- Modify: `CHANGELOG.md`
- Modify: `scripts/test_repository_hygiene.py`

- [ ] **Step 1: Write a failing documentation-boundary test**

  Extend `test_public_repository_metadata_exists()` to require `docs/transcription.md`. Add a test that reads the guide and asserts it contains:

  - `python3 scripts/podcast_transcriber.py --check`
  - `--audio`
  - `--output-dir`
  - `requirements-transcription.txt`
  - `ffmpeg`
  - `OpenClaw`
  - a statement that the project does not download audio

- [ ] **Step 2: Run the hygiene test to verify RED**

  ```bash
  python3 scripts/test_repository_hygiene.py
  ```

- [ ] **Step 3: Write `docs/transcription.md`**

  Include these complete sections:

  1. Optional-layer boundary and no-download statement.
  2. Shared environment setup using the existing project virtual environment.
  3. `ffmpeg` installation examples for Homebrew and Debian/Ubuntu, labelled as examples rather than an exhaustive platform matrix.
  4. `python3 -m pip install -r requirements-transcription.txt`.
  5. Apple Silicon MLX and portable OpenAI backend explanations.
  6. `--check` example and explanation that it does not download a model.
  7. One local-file command with absolute input/output paths.
  8. Output file and metadata interpretation.
  9. First-run model download, time, memory, and disk warning.
  10. Generic agent prompt instructing the agent to process exactly one local file, run check first, parse JSON status, avoid configuration changes, and report `transcript.txt`.
  11. Privacy, local storage, copyright, and ignored-directory guidance.
  12. Troubleshooting for missing ffmpeg, missing backend, slow CPU fallback, empty transcript, and reuse/force behavior.

- [ ] **Step 4: Update project-facing documentation**

  - Add a README section titled `可选：用本地 Whisper 转写已下载音频`, with the check and transcription commands plus a link to `docs/transcription.md`.
  - State that the user or their agent supplies the local audio file and that automatic downloading is not included.
  - Add the transcription layer as a separate optional branch in `docs/architecture.md`, not as a cron step.
  - Add the module boundary to `AGENTS.md`: no downloads, no RSS, no OpenClaw, no Feishu, and tests must inject fake backends.
  - Add an `Unreleased` / `Added` entry to `CHANGELOG.md` for the local-file transcription CLI and agent guide.

- [ ] **Step 5: Run documentation and hygiene tests to verify GREEN**

  ```bash
  python3 scripts/test_repository_hygiene.py
  git diff --check -- README.md AGENTS.md CHANGELOG.md docs scripts/test_repository_hygiene.py
  ```

- [ ] **Step 6: Commit documentation**

  ```bash
  git add README.md AGENTS.md CHANGELOG.md docs/architecture.md docs/transcription.md scripts/test_repository_hygiene.py
  git commit -m "docs: add local Whisper transcription guide"
  ```

## Task 7: Complete Safe Regression And Release Readiness

**Files:**
- Modify only files needed to fix failures introduced by Tasks 1-6.

- [ ] **Step 1: Run focused transcription verification**

  ```bash
  python3 scripts/test_podcast_transcriber.py
  python3 scripts/test_policy_config.py
  python3 scripts/test_pipeline_paths.py
  python3 scripts/test_repository_hygiene.py
  python3 -m py_compile scripts/podcast_transcriber.py
  python3 -m py_compile scripts/test_podcast_transcriber.py
  ```

- [ ] **Step 2: Run the complete hermetic suite**

  ```bash
  python3 scripts/run_safe_regression.py
  ```

  Confirm the suite does not install or import real Whisper packages, process audio, fetch RSS, call OpenClaw, call Feishu, or write ignored runtime directories.

- [ ] **Step 3: Inspect scope and whitespace**

  ```bash
  git status --short
  git diff --check
  git log --oneline --decorate -8
  git diff main...HEAD --stat
  git diff main...HEAD -- scripts/podcast_screener.py scripts/podcast_screener_cron.sh scripts/rss_adapter.py scripts/deliver_weekly_report_to_feishu.py scripts/feishu_notify.py config
  ```

  The final command must be empty: the implementation must not change weekly production, RSS, Feishu, OpenClaw, or tracked configuration.

- [ ] **Step 4: Commit only regression fixes when necessary**

  If the safe suite exposes an implementation defect, add a failing regression test first, fix only that defect, rerun focused and complete suites, then commit:

  ```bash
  git add scripts/podcast_transcriber.py scripts/test_podcast_transcriber.py
  git commit -m "test: harden local transcription regression"
  ```

  If no fixes are needed, do not create an empty commit.

- [ ] **Step 5: Prepare manual dry-run handoff without executing it**

  Report the exact separately authorized command for a short local audio file:

  ```bash
  python3 scripts/podcast_transcriber.py --check
  python3 scripts/podcast_transcriber.py \
    --audio /absolute/path/to/short-local-audio.mp3 \
    --output-dir /absolute/path/to/manual-transcription-check
  ```

  Do not run this real-model dry-run during implementation unless the user gives explicit authorization after reviewing the hermetic results.
