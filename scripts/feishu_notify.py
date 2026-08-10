#!/usr/bin/env python3
"""
feishu_notify.py v2.2 — 飞书群摘要通知
只发送简短摘要，不发长列表。
摘要内容：week_id / 窗口 / total/full/preview/skip / fetch_errors / top3 / 飞书链接 / run_id
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from latest_result_store import read_result as read_latest_result
from latest_result_store import write_notification_meta as store_write_notification_meta
from pipeline_paths import get_pipeline_paths

_RUNTIME_PATHS = get_pipeline_paths()
PIPELINE_DIR = str(_RUNTIME_PATHS.pipeline_dir)
STATE_DIR = str(_RUNTIME_PATHS.state_dir)
CONFIG_DIR = str(_RUNTIME_PATHS.config_dir)
RESULT_JSON = str(_RUNTIME_PATHS.outputs_dir / "latest_screening_result.json")
FEISHU_CONFIG = os.path.join(CONFIG_DIR, "feishu_config.json")
DELIVERY_LOG = os.path.join(STATE_DIR, "delivery_log.jsonl")
TZ_SH = timezone(timedelta(hours=8))


def get_webhook_url() -> str:
    """Read the webhook from environment or the ignored local config file."""
    env_url = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
    if env_url:
        return env_url
    if os.path.exists(FEISHU_CONFIG):
        with open(FEISHU_CONFIG) as f:
            cfg = json.load(f)
        url = cfg.get("webhook_url", "")
        if url:
            return url
    raise ValueError(
        "[feishu_notify] ERROR: feishu_config.json 中 webhook_url 未配置或为空。"
        "请编辑 config/feishu_config.json 填入飞书群机器人 Webhook URL。"
    )


def send_post_message(webhook_url: str, title: str, paragraphs: list) -> bool:
    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": paragraphs
                }
            }
        }
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(webhook_url, data=data)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.load(resp)
            if result.get("code") != 0 and result.get("StatusCode") != 0:
                print(f"[feishu_notify] ERROR response: {result}", file=sys.stderr)
                return False
            return True
    except Exception as e:
        print(f"[feishu_notify] ERROR: {e}", file=sys.stderr)
        return False


def tag_text(text: str) -> dict:
    return {"tag": "text", "text": text}


def tag_a(text: str, href: str) -> dict:
    return {"tag": "a", "text": text, "href": href}


def is_successfully_delivered(delivery_meta: dict) -> bool:
    return (
        isinstance(delivery_meta, dict)
        and delivery_meta.get("delivery_status") == "success"
        and bool(delivery_meta.get("feishu_doc_id"))
        and bool(delivery_meta.get("feishu_doc_url"))
    )


def is_successfully_notified(notification_meta: dict, result_data: dict) -> bool:
    if not isinstance(notification_meta, dict):
        return False
    if notification_meta.get("notification_status") != "success":
        return False
    if notification_meta.get("run_id") != result_data.get("run_id"):
        return False
    if notification_meta.get("week_id") != result_data.get("week_id"):
        return False
    doc_id = notification_meta.get("feishu_doc_id", "")
    doc_url = notification_meta.get("feishu_doc_url", "")
    if not doc_id or not doc_url:
        return False
    delivery_meta = result_data.get("delivery_meta", {}) or {}
    if delivery_meta.get("feishu_doc_id") and delivery_meta.get("feishu_doc_id") != doc_id:
        return False
    if delivery_meta.get("feishu_doc_url") and delivery_meta.get("feishu_doc_url") != doc_url:
        return False
    return True


def validate_result_data(result_data: dict):
    required = ["run_id", "week_id", "window_start", "window_end"]
    missing = [k for k in required if not result_data.get(k)]
    if missing:
        raise ValueError(f"[feishu_notify] ERROR: latest result missing required fields: {missing}")


def has_webhook_config() -> bool:
    if os.environ.get("FEISHU_WEBHOOK_URL", "").strip():
        return True
    if not os.path.exists(FEISHU_CONFIG):
        return False
    try:
        with open(FEISHU_CONFIG) as f:
            cfg = json.load(f)
        return bool(cfg.get("webhook_url", ""))
    except Exception:
        return False


def build_notification_payload(data: dict):
    if not os.path.exists(RESULT_JSON):
        print(f"[feishu_notify] WARN: {RESULT_JSON} not found", file=sys.stderr)
        sys.exit(0)

    week_id = data.get("week_id", "unknown")
    window_start = data.get("window_start", "")[:10]
    window_end = data.get("window_end", "")[:10]
    total = data.get("total_episodes", 0)
    full_n = len(data.get("full", []))
    preview_n = len(data.get("preview", []))
    skip_n = len(data.get("skip", []))
    fetch_errors = data.get("fetch_errors", [])
    translation_summary = data.get("show_notes_translation_summary", {}) or {}
    run_id = data.get("run_id", "")

    delivery_meta = data.get("delivery_meta", {})
    doc_url = delivery_meta.get("feishu_doc_url", "")
    doc_id = delivery_meta.get("feishu_doc_id", "")

    paragraphs = []

    # 基本信息行
    window_str = f"{window_start} ~ {window_end}"
    paragraphs.append([tag_text(f"📅 {week_id} | 窗口：{window_str}")])
    paragraphs.append([tag_text(f"共扫描 {total} 期 | ✅Full {full_n} | 🔍Preview {preview_n} | ⏭️Skip {skip_n}")])
    if fetch_errors:
        paragraphs.append([tag_text(f"⚠️ Fetch 错误：{len(fetch_errors)} 个 → {', '.join(fetch_errors)}")])
    eligible_n = int(translation_summary.get("eligible_count") or 0)
    visible_translation_n = int(translation_summary.get("visible_translation_count") or 0)
    translation_failed_n = int(translation_summary.get("failed_count") or 0)
    if eligible_n:
        paragraphs.append([
            tag_text(f"🌐 Show Notes 翻译：{visible_translation_n}/{eligible_n}")
        ])
    if translation_failed_n:
        paragraphs.append([
            tag_text(f"⚠️ Show Notes 翻译：{translation_failed_n} 期回退原文，请查看周报")
        ])
    paragraphs.append([tag_text("")])

    # Top 3 推荐：full + preview 合并，按分数降序取前 3
    top3 = sorted(
        data.get("full", []) + data.get("preview", []),
        key=lambda x: x.get("score", 0),
        reverse=True
    )[:3]

    if top3:
        paragraphs.append([tag_text("🏆 Top 推荐：")])
        for ep in top3:
            score = ep.get("score", 0)
            badge = "✅" if ep.get("decision") == "full" else "🔍"
            mins = ep.get("duration_minutes", 0)
            line = f"{badge} {ep.get('podcast_name', '')} | {ep.get('episode_title', '')[:30]} | {mins}min | {score}分"
            paragraphs.append([tag_text(line)])

    paragraphs.append([tag_text("")])

    # 飞书文档链接
    if doc_url:
        paragraphs.append([
            tag_text("📄 飞书周报："),
            tag_a(doc_url, doc_url)
        ])
    else:
        paragraphs.append([tag_text("📄 飞书周报：生成中（稍后查看本地文件）")])

    paragraphs.append([tag_text(f"🔧 run_id：{run_id}")])
    paragraphs.append([tag_text("由 cron 自动调度，每周日 22:10 执行")])

    title = f"🎧 播客周报 {week_id} | {'✅有推荐' if full_n + preview_n > 0 else '⏭️无推荐'}"
    summary = {
        "full_n": full_n,
        "preview_n": preview_n,
        "skip_n": skip_n,
        "translation_failed_n": translation_failed_n,
    }
    return title, paragraphs, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send weekly podcast report summary to Feishu group")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate notification inputs without calling webhook or writing state")
    parser.add_argument("--force", action="store_true",
                        help="Resend notification even when successful notification_meta already exists")
    args = parser.parse_args(argv)

    if not os.path.exists(RESULT_JSON):
        print(f"[feishu_notify] WARN: {RESULT_JSON} not found", file=sys.stderr)
        sys.exit(0)

    data = read_latest_result(RESULT_JSON)

    try:
        validate_result_data(data)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    run_id = data.get("run_id", "")
    week_id = data.get("week_id", "unknown")
    delivery_meta = data.get("delivery_meta", {}) or {}
    notification_meta = data.get("notification_meta", {}) or {}
    delivery_success = is_successfully_delivered(delivery_meta)
    notification_success = is_successfully_notified(notification_meta, data)
    doc_url = delivery_meta.get("feishu_doc_url", "")
    doc_id = delivery_meta.get("feishu_doc_id", "")

    if notification_success and not args.force and not args.dry_run:
        print(f"[feishu_notify] SKIP: already notified run_id={run_id} doc_url={notification_meta.get('feishu_doc_url', '')}")
        return "skip"

    if notification_meta and not notification_success and not args.force and not args.dry_run:
        status = notification_meta.get("notification_status", "unknown")
        print(f"[feishu_notify] ERROR: existing non-success notification_meta status={status}; use --force to resend", file=sys.stderr)
        sys.exit(1)

    title = None
    paragraphs = None
    summary = {}
    if args.dry_run or delivery_success or args.force:
        title, paragraphs, summary = build_notification_payload(data)

    if args.dry_run:
        if notification_success and not args.force:
            action = "skip"
        elif notification_success and args.force:
            action = "force-send"
        elif not delivery_success:
            action = "requires-delivery"
        elif notification_meta and not args.force:
            action = "requires-force"
        else:
            action = "send"
        print("[feishu_notify] DRY-RUN")
        print(f"[feishu_notify] run_id={run_id}")
        print(f"[feishu_notify] week_id={week_id}")
        print(f"[feishu_notify] has_delivery_success={delivery_success}")
        print(f"[feishu_notify] has_doc_url={bool(doc_url)}")
        print(f"[feishu_notify] has_notification_meta={bool(notification_meta)}")
        print(f"[feishu_notify] has_webhook_config={has_webhook_config()}")
        print(f"[feishu_notify] title={title}")
        print(f"[feishu_notify] paragraphs_count={len(paragraphs)}")
        print(f"[feishu_notify] action={action}")
        return "dry-run"

    if not delivery_success:
        print("[feishu_notify] ERROR: successful delivery_meta with feishu_doc_id and feishu_doc_url is required before notification", file=sys.stderr)
        sys.exit(1)

    old_notification_status = notification_meta.get("notification_status", "") if notification_meta else ""
    old_notified_at = notification_meta.get("notified_at", "") if notification_meta else ""

    webhook_url = get_webhook_url()
    try:
        ok = send_post_message(webhook_url, title, paragraphs)
        error = "" if ok else "webhook returned failure"
    except Exception as e:
        ok = False
        error = str(e)

    attempted_at = datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z")

    if ok:
        print(f"[feishu_notify] SUCCESS: {week_id} | Full={summary.get('full_n', 0)} Preview={summary.get('preview_n', 0)} Skip={summary.get('skip_n', 0)}")
        store_write_notification_meta(RESULT_JSON, data, {
            "notification_status": "success",
            "run_id": run_id,
            "week_id": week_id,
            "feishu_doc_id": doc_id,
            "feishu_doc_url": doc_url,
            "notified_at": attempted_at
        })
        _log_delivery(run_id, week_id, "feishu_group_post", "success", doc_url, doc_id, {
            "forced": bool(args.force),
            "old_notification_status": old_notification_status,
            "old_notified_at": old_notified_at
        })
        return "success"
    else:
        print("[feishu_notify] FAIL: 飞书群消息发送失败", file=sys.stderr)
        store_write_notification_meta(RESULT_JSON, data, {
            "notification_status": "error",
            "run_id": run_id,
            "week_id": week_id,
            "feishu_doc_id": doc_id,
            "feishu_doc_url": doc_url,
            "error": error,
            "attempted_at": attempted_at
        })
        _log_delivery(run_id, week_id, "feishu_group_post", "error", doc_url, doc_id, {
            "error": error,
            "forced": bool(args.force),
            "old_notification_status": old_notification_status,
            "old_notified_at": old_notified_at
        })
        sys.exit(1)


def _log_delivery(run_id, week_id, event, status, doc_url, doc_id, extra=None):
    try:
        log_path = DELIVERY_LOG
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        record = {
            "timestamp": datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            "run_id": run_id,
            "week_id": week_id,
            "status": status,
            "doc_url": doc_url,
            "doc_id": doc_id
        }
        if extra:
            record.update(extra)
        with open(log_path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
