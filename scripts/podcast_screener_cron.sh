#!/bin/bash
# podcast-weekly-screener cron job
# Runs every Sunday at 22:10 (Asia/Shanghai)
# 执行顺序：podcast_screener.py → deliver_weekly_report_to_feishu.py → feishu_notify.py

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE_DIR="${PODCAST_PIPELINE_HOME:-${PIPELINE_DIR:-$SCRIPT_ROOT}}"
if [ ! -d "$PIPELINE_DIR" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] missing PIPELINE_DIR=$PIPELINE_DIR" >&2
    exit 1
fi

cd "$PIPELINE_DIR" || exit 1
export PODCAST_PIPELINE_HOME="$PIPELINE_DIR"

STATE_DIR="$PIPELINE_DIR/state"
LOCK_DIR="$STATE_DIR/podcast_screener_cron.lock"
LOG="$PIPELINE_DIR/logs/screener_cron.log"
STDOUT_LOG="$PIPELINE_DIR/logs/screener_stdout.log"
STDERR="$PIPELINE_DIR/logs/screener_stderr.log"

# 环境准备
mkdir -p "$PIPELINE_DIR/outputs" "$STATE_DIR" "$PIPELINE_DIR/logs"
PROXY_SETTING="${PODCAST_PIPELINE_PROXY:-http://127.0.0.1:7890}"
if [ "$PROXY_SETTING" = "off" ] || [ "$PROXY_SETTING" = "none" ] || [ "$PROXY_SETTING" = "disabled" ]; then
    unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
    PROXY_MODE="disabled"
else
    export http_proxy="$PROXY_SETTING"
    export https_proxy="$PROXY_SETTING"
    export all_proxy="${PODCAST_PIPELINE_ALL_PROXY:-socks5://127.0.0.1:7890}"
    export HTTP_PROXY="$http_proxy"
    export HTTPS_PROXY="$https_proxy"
    export ALL_PROXY="$all_proxy"
    PROXY_MODE="configured"
fi

NVM_BIN=""
for candidate in "$HOME"/.nvm/versions/node/*/bin; do
    if [ -d "$candidate" ]; then
        NVM_BIN="$candidate"
    fi
done
BASE_RUNTIME_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/anaconda3/bin:$HOME/miniconda3/bin"
export PATH="${PODCAST_PIPELINE_EXTRA_PATH:+$PODCAST_PIPELINE_EXTRA_PATH:}${NVM_BIN:+$NVM_BIN:}$BASE_RUNTIME_PATH"
PYTHON_BIN="${PODCAST_PIPELINE_PYTHON:-$(command -v python3)}"
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] python executable unavailable: $PYTHON_BIN" >&2
    exit 1
fi

log_runtime_diagnostics() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] PIPELINE_DIR=$PIPELINE_DIR" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] PODCAST_PIPELINE_HOME=$PODCAST_PIPELINE_HOME" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] pwd=$(pwd)" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] head=$(git log -1 --oneline 2>/dev/null || true)" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] python=$PYTHON_BIN" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] python_version=$("$PYTHON_BIN" --version 2>&1 || true)" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] openclaw=$(command -v openclaw || true)" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] proxy_mode=$PROXY_MODE" >> "$LOG"
    "$PYTHON_BIN" - "$PIPELINE_DIR/config/policy.yaml" "$SCRIPT_ROOT/scripts" <<'PY' >> "$LOG" 2>&1 || true
from pathlib import Path
import sys

try:
    sys.path.insert(0, sys.argv[2])
    from policy_config import load_policy_config

    policy = load_policy_config(Path(sys.argv[1]))
    cfg = policy.get("show_notes_translation") or {}
    print(f"[cron-wrapper] show_notes_translation.enabled={cfg.get('enabled')}")
    print(f"[cron-wrapper] show_notes_translation.mode={cfg.get('mode')}")
    print(f"[cron-wrapper] show_notes_translation.agent_id={cfg.get('agent_id')}")
    print(f"[cron-wrapper] show_notes_translation.model={cfg.get('model')}")
except Exception as exc:
    print(f"[cron-wrapper] show_notes_translation.diagnostic_error={type(exc).__name__}: {exc}")
PY
}

log_translation_summary() {
    "$PYTHON_BIN" - "$PIPELINE_DIR/outputs/latest_screening_result.json" <<'PY' >> "$LOG" 2>&1 || true
import json
import sys
from pathlib import Path

try:
    result = json.loads(Path(sys.argv[1]).read_text())
    summary = result.get("show_notes_translation_summary") or {}
    eligible = int(summary.get("eligible_count") or 0)
    visible = int(summary.get("visible_translation_count") or 0)
    failed = int(summary.get("failed_count") or 0)
    cache_hits = int(summary.get("cache_hit_count") or 0)
    print(
        f"[cron-wrapper] TRANSLATION_SUMMARY eligible={eligible} "
        f"visible={visible} failed={failed} cache_hits={cache_hits}"
    )
    if failed:
        titles = [
            item.get("title", "")
            for item in summary.get("failed_episodes", [])
            if isinstance(item, dict) and item.get("title")
        ]
        print(
            f"[cron-wrapper] TRANSLATION_WARNING failed={failed} "
            f"episodes={' | '.join(titles[:5])}"
        )
except Exception as exc:
    print(f"[cron-wrapper] TRANSLATION_SUMMARY_ERROR {type(exc).__name__}: {exc}")
PY
}

# ── 防重复触发：原子 lock directory，避免匹配 launchd 自身 label ─────────
if ! mkdir "$LOCK_DIR"; then
    if [ -f "$LOCK_DIR/pid" ]; then
        OLD_PID="$(cat "$LOCK_DIR/pid")"
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] another podcast screener run is active pid=$OLD_PID; skip this run" >> "$LOG"
            exit 0
        fi

        echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] stale lock detected pid=$OLD_PID; retry lock acquisition" >> "$LOG"
        rm -f "$LOCK_DIR/pid"
        rmdir "$LOCK_DIR" 2>/dev/null || true
        if ! mkdir "$LOCK_DIR"; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] lock retry failed; skip this run" >> "$LOG"
            exit 0
        fi
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] [cron-wrapper] lock exists without pid; skip this run" >> "$LOG"
        exit 0
    fi
fi

echo "$$" > "$LOCK_DIR/pid"

cleanup_lock() {
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup_lock EXIT
trap 'cleanup_lock; exit 130' INT TERM HUP

# 追加运行标记
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] TASK_TRIGGERED - weekly podcast screener v2.2" >> "$LOG"
log_runtime_diagnostics

if [ "${PODCAST_SCREENER_CRON_DRY_RUN:-}" = "1" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] DRY_RUN: would run STEP1 podcast_screener, STEP2 deliver_weekly_report_to_feishu, STEP3 feishu_notify" >> "$LOG"
    echo "[cron-wrapper] DRY_RUN: would run STEP1/STEP2/STEP3"
    exit 0
fi

# ── Step 1: 播客筛选 ─────────────────────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] STEP1_START: podcast_screener" >> "$LOG"
"$PYTHON_BIN" "$PIPELINE_DIR/scripts/podcast_screener.py" >> "$STDOUT_LOG" 2>> "$STDERR"
SCREENER_EXIT=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] STEP1_END: podcast_screener exit=$SCREENER_EXIT" >> "$LOG"
if [ "$SCREENER_EXIT" -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] CRON_JOB_ABORTED step=podcast_screener exit=$SCREENER_EXIT" >> "$LOG"
    exit "$SCREENER_EXIT"
fi
log_translation_summary

# ── Step 2: 写入飞书文档 ─────────────────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] STEP2_START: deliver_weekly_report_to_feishu" >> "$LOG"
"$PYTHON_BIN" "$PIPELINE_DIR/scripts/deliver_weekly_report_to_feishu.py" >> "$STDOUT_LOG" 2>> "$STDERR"
DELIVER_EXIT=$?
if [ $DELIVER_EXIT -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] STEP2_END: deliver_weekly_report_to_feishu exit=0" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] STEP2_END: deliver_weekly_report_to_feishu exit=$DELIVER_EXIT" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] CRON_JOB_ABORTED step=deliver_weekly_report_to_feishu exit=$DELIVER_EXIT" >> "$LOG"
    exit "$DELIVER_EXIT"
fi

# ── Step 3: 飞书群通知 ───────────────────────────────────────────
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] STEP3_START: feishu_notify" >> "$LOG"
"$PYTHON_BIN" "$PIPELINE_DIR/scripts/feishu_notify.py" >> "$STDOUT_LOG" 2>> "$STDERR"
NOTIFY_EXIT=$?
if [ $NOTIFY_EXIT -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] STEP3_END: feishu_notify exit=0" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] CRON_JOB_COMPLETED" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] STEP3_END: feishu_notify exit=$NOTIFY_EXIT" >> "$LOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] CRON_JOB_COMPLETED_WITH_NOTIFY_ERROR" >> "$LOG"
    exit "$NOTIFY_EXIT"
fi

exit 0
