#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_SCRIPT="$ROOT_DIR/scripts/podcast_screener_cron.sh"
TEST_SCRIPT="$ROOT_DIR/scripts/test_podcast_screener_cron_lock.sh"

TMP_DIRS=()
cleanup() {
    if [ "${#TMP_DIRS[@]}" -gt 0 ]; then
        for d in "${TMP_DIRS[@]}"; do
            rm -rf "$d"
        done
    fi
}
trap cleanup EXIT

new_tmp_pipeline() {
    local d
    d="$(mktemp -d)"
    mkdir -p "$d/config"
    cat > "$d/config/policy.yaml" <<'YAML'
show_notes_translation:
  enabled: false
  mode: mock
YAML
    cat > "$d/config/policy.local.yaml" <<'YAML'
show_notes_translation:
  enabled: true
  mode: openclaw
  agent_id: main
  model: minimax-portal/MiniMax-M2.7
YAML
    TMP_DIRS+=("$d")
    printf '%s\n' "$d"
}

run_dry_cron() {
    local pipeline_dir="$1"
    PIPELINE_DIR="$pipeline_dir" PODCAST_SCREENER_CRON_DRY_RUN=1 bash "$CRON_SCRIPT"
}

assert_contains() {
    local file="$1"
    local needle="$2"
    if ! grep -q "$needle" "$file"; then
        echo "ASSERT FAILED: expected '$needle' in $file" >&2
        echo "--- $file ---" >&2
        cat "$file" >&2
        exit 1
    fi
}

assert_not_contains() {
    local file="$1"
    local needle="$2"
    if grep -q "$needle" "$file"; then
        echo "ASSERT FAILED: did not expect '$needle' in $file" >&2
        echo "--- $file ---" >&2
        cat "$file" >&2
        exit 1
    fi
}

prepare_fake_runtime() {
    local tmp="$1"
    local fake_python="$tmp/fake-python3"
    local real_python
    real_python="$(command -v python3)"
    mkdir -p "$tmp/scripts"
    : > "$tmp/scripts/podcast_screener.py"
    : > "$tmp/scripts/deliver_weekly_report_to_feishu.py"
    : > "$tmp/scripts/feishu_notify.py"
    cat > "$fake_python" <<EOF
#!/bin/bash
if [ "\${1:-}" = "-" ] || [ "\${1:-}" = "--version" ]; then
    exec "$real_python" "\$@"
fi
name="\$(basename "\${1:-unknown}")"
printf '%s\n' "\$name" >> "\$FAKE_CALL_LOG"
case "\$name" in
    podcast_screener.py) exit "\${FAKE_SCREENER_EXIT:-0}" ;;
    deliver_weekly_report_to_feishu.py) exit "\${FAKE_DELIVER_EXIT:-0}" ;;
    feishu_notify.py) exit "\${FAKE_NOTIFY_EXIT:-0}" ;;
    *) exit 99 ;;
esac
EOF
    chmod +x "$fake_python"
    printf '%s\n' "$fake_python"
}

run_fake_cron() {
    local pipeline_dir="$1"
    local fake_python="$2"
    local call_log="$3"
    local screener_exit="$4"
    local deliver_exit="$5"
    local notify_exit="$6"
    local rc
    set +e
    PIPELINE_DIR="$pipeline_dir" \
    PODCAST_PIPELINE_PYTHON="$fake_python" \
    FAKE_CALL_LOG="$call_log" \
    FAKE_SCREENER_EXIT="$screener_exit" \
    FAKE_DELIVER_EXIT="$deliver_exit" \
    FAKE_NOTIFY_EXIT="$notify_exit" \
        bash "$CRON_SCRIPT" >/dev/null 2>/dev/null
    rc=$?
    set -e
    printf '%s\n' "$rc"
}

assert_call_order() {
    local file="$1"
    local expected="$2"
    local actual=""
    if [ -f "$file" ]; then
        actual="$(paste -sd, "$file")"
    fi
    if [ "$actual" != "$expected" ]; then
        echo "ASSERT FAILED: expected call order '$expected', got '$actual'" >&2
        exit 1
    fi
}

assert_runtime_diagnostics() {
    local log="$1"
    local pipeline_dir="$2"

    assert_contains "$log" "PIPELINE_DIR=$pipeline_dir"
    assert_contains "$log" "PODCAST_PIPELINE_HOME=$pipeline_dir"
    assert_contains "$log" "pwd=$pipeline_dir"
    assert_contains "$log" "head="
    assert_contains "$log" "python="
    assert_contains "$log" "python_version="
    assert_contains "$log" "openclaw="
    assert_contains "$log" "proxy_mode="
    assert_contains "$log" "show_notes_translation.enabled=True"
    assert_contains "$log" "show_notes_translation.mode=openclaw"
    assert_contains "$log" "show_notes_translation.agent_id=main"
    assert_contains "$log" "show_notes_translation.model=minimax-portal/MiniMax-M2.7"
}

test_script_relative_default_root() {
    local tmp log
    tmp="$(new_tmp_pipeline)"
    mkdir -p "$tmp/scripts"
    cp "$CRON_SCRIPT" "$tmp/scripts/podcast_screener_cron.sh"
    cp "$ROOT_DIR/scripts/policy_config.py" "$tmp/scripts/policy_config.py"
    log="$tmp/logs/screener_cron.log"

    env -u PIPELINE_DIR -u PODCAST_PIPELINE_HOME \
        PODCAST_SCREENER_CRON_DRY_RUN=1 \
        bash "$tmp/scripts/podcast_screener_cron.sh" >/dev/null 2>/dev/null

    assert_runtime_diagnostics "$log" "$tmp"
    echo "ok script-relative default root"
}

test_extra_path_exposes_openclaw() {
    local tmp bin log
    tmp="$(new_tmp_pipeline)"
    bin="$tmp/custom-bin"
    log="$tmp/logs/screener_cron.log"
    mkdir -p "$bin"
    cat > "$bin/openclaw" <<'SH'
#!/bin/bash
exit 0
SH
    chmod +x "$bin/openclaw"

    PIPELINE_DIR="$tmp" \
    PODCAST_PIPELINE_EXTRA_PATH="$bin" \
    PODCAST_SCREENER_CRON_DRY_RUN=1 \
        bash "$CRON_SCRIPT" >/dev/null 2>/dev/null

    assert_contains "$log" "openclaw=$bin/openclaw"
    echo "ok extra path exposes openclaw"
}

test_proxy_can_be_disabled() {
    local tmp log
    tmp="$(new_tmp_pipeline)"
    log="$tmp/logs/screener_cron.log"

    PIPELINE_DIR="$tmp" \
    PODCAST_PIPELINE_PROXY="off" \
    PODCAST_SCREENER_CRON_DRY_RUN=1 \
        bash "$CRON_SCRIPT" >/dev/null 2>/dev/null

    assert_contains "$log" "proxy_mode=disabled"
    echo "ok proxy can be disabled"
}

test_no_fixed_node_version_path() {
    assert_not_contains "$CRON_SCRIPT" ".nvm/versions/node/v24.14.0/bin"
    echo "ok no fixed node version path"
}

test_clean_lock() {
    local tmp log lock
    tmp="$(new_tmp_pipeline)"
    log="$tmp/logs/screener_cron.log"
    lock="$tmp/state/podcast_screener_cron.lock"

    run_dry_cron "$tmp" >/dev/null 2>/dev/null

    if [ -e "$lock" ]; then
        echo "ASSERT FAILED: clean lock case should remove lock after dry-run" >&2
        exit 1
    fi
    assert_contains "$log" "DRY_RUN"
    assert_runtime_diagnostics "$log" "$tmp"
    echo "ok clean lock"
}

test_active_pid_lock() {
    local tmp log lock
    tmp="$(new_tmp_pipeline)"
    log="$tmp/logs/screener_cron.log"
    lock="$tmp/state/podcast_screener_cron.lock"
    mkdir -p "$lock"
    echo "$$" > "$lock/pid"

    run_dry_cron "$tmp" >/dev/null 2>/dev/null

    if [ ! -d "$lock" ] || [ ! -f "$lock/pid" ]; then
        echo "ASSERT FAILED: active pid case should keep lock and pid" >&2
        exit 1
    fi
    assert_contains "$log" "another podcast screener run is active"
    echo "ok active pid"
}

test_stale_pid_lock() {
    local tmp log lock
    tmp="$(new_tmp_pipeline)"
    log="$tmp/logs/screener_cron.log"
    lock="$tmp/state/podcast_screener_cron.lock"
    mkdir -p "$lock"
    echo "99999999" > "$lock/pid"

    run_dry_cron "$tmp" >/dev/null 2>/dev/null

    if [ -e "$lock" ]; then
        echo "ASSERT FAILED: stale pid case should reacquire and remove lock after dry-run" >&2
        exit 1
    fi
    assert_contains "$log" "stale lock detected"
    assert_contains "$log" "DRY_RUN"
    echo "ok stale pid"
}

test_lock_without_pid() {
    local tmp log lock
    tmp="$(new_tmp_pipeline)"
    log="$tmp/logs/screener_cron.log"
    lock="$tmp/state/podcast_screener_cron.lock"
    mkdir -p "$lock"

    run_dry_cron "$tmp" >/dev/null 2>/dev/null

    if [ ! -d "$lock" ]; then
        echo "ASSERT FAILED: lock without pid case should keep lock directory" >&2
        exit 1
    fi
    if [ -f "$lock/pid" ]; then
        echo "ASSERT FAILED: lock without pid case should not create pid" >&2
        exit 1
    fi
    assert_contains "$log" "lock exists without pid"
    echo "ok lock without pid"
}

test_all_steps_success() {
    local tmp fake call_log log rc
    tmp="$(new_tmp_pipeline)"
    fake="$(prepare_fake_runtime "$tmp")"
    call_log="$tmp/calls.log"
    log="$tmp/logs/screener_cron.log"

    rc="$(run_fake_cron "$tmp" "$fake" "$call_log" 0 0 0)"

    [ "$rc" = "0" ] || { echo "ASSERT FAILED: success rc=$rc" >&2; exit 1; }
    assert_call_order "$call_log" "podcast_screener.py,deliver_weekly_report_to_feishu.py,feishu_notify.py"
    assert_contains "$log" "CRON_JOB_COMPLETED"
    assert_not_contains "$log" "CRON_JOB_ABORTED"
    [ ! -e "$tmp/state/podcast_screener_cron.lock" ]
    echo "ok all steps success"
}

test_screener_failure_stops_delivery() {
    local tmp fake call_log log rc
    tmp="$(new_tmp_pipeline)"
    fake="$(prepare_fake_runtime "$tmp")"
    call_log="$tmp/calls.log"
    log="$tmp/logs/screener_cron.log"

    rc="$(run_fake_cron "$tmp" "$fake" "$call_log" 7 0 0)"

    [ "$rc" = "7" ] || { echo "ASSERT FAILED: screener failure rc=$rc" >&2; exit 1; }
    assert_call_order "$call_log" "podcast_screener.py"
    assert_contains "$log" "CRON_JOB_ABORTED step=podcast_screener exit=7"
    assert_not_contains "$log" "STEP2_START"
    assert_not_contains "$log" "STEP3_START"
    [ ! -e "$tmp/state/podcast_screener_cron.lock" ]
    echo "ok screener failure stops delivery"
}

test_delivery_failure_stops_notification() {
    local tmp fake call_log log rc
    tmp="$(new_tmp_pipeline)"
    fake="$(prepare_fake_runtime "$tmp")"
    call_log="$tmp/calls.log"
    log="$tmp/logs/screener_cron.log"

    rc="$(run_fake_cron "$tmp" "$fake" "$call_log" 0 8 0)"

    [ "$rc" = "8" ] || { echo "ASSERT FAILED: delivery failure rc=$rc" >&2; exit 1; }
    assert_call_order "$call_log" "podcast_screener.py,deliver_weekly_report_to_feishu.py"
    assert_contains "$log" "CRON_JOB_ABORTED step=deliver_weekly_report_to_feishu exit=8"
    assert_not_contains "$log" "STEP3_START"
    [ ! -e "$tmp/state/podcast_screener_cron.lock" ]
    echo "ok delivery failure stops notification"
}

test_notification_failure_is_propagated() {
    local tmp fake call_log log rc
    tmp="$(new_tmp_pipeline)"
    fake="$(prepare_fake_runtime "$tmp")"
    call_log="$tmp/calls.log"
    log="$tmp/logs/screener_cron.log"

    rc="$(run_fake_cron "$tmp" "$fake" "$call_log" 0 0 9)"

    [ "$rc" = "9" ] || { echo "ASSERT FAILED: notify failure rc=$rc" >&2; exit 1; }
    assert_call_order "$call_log" "podcast_screener.py,deliver_weekly_report_to_feishu.py,feishu_notify.py"
    assert_contains "$log" "CRON_JOB_COMPLETED_WITH_NOTIFY_ERROR"
    assert_not_contains "$log" "CRON_JOB_COMPLETED$"
    [ ! -e "$tmp/state/podcast_screener_cron.lock" ]
    echo "ok notification failure propagated"
}

bash -n "$CRON_SCRIPT"
bash -n "$TEST_SCRIPT"

test_clean_lock
test_script_relative_default_root
test_extra_path_exposes_openclaw
test_proxy_can_be_disabled
test_no_fixed_node_version_path
test_active_pid_lock
test_stale_pid_lock
test_lock_without_pid
test_all_steps_success
test_screener_failure_stops_delivery
test_delivery_failure_stops_notification
test_notification_failure_is_propagated

echo "ALL CRON LOCK TESTS PASSED"
