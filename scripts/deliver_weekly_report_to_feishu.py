#!/usr/bin/env python3
"""
deliver_weekly_report_to_feishu.py v2.2
飞书周报交付脚本：读取本地报告 → 写入飞书文档 → 回写 delivery_meta
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from feishu_blocks_renderer import build_feishu_blocks as renderer_build_feishu_blocks
from latest_result_store import read_result as read_latest_result
from latest_result_store import write_delivery_meta as store_write_delivery_meta
from podcast_screener import build_show_notes_translation_render_options
from pipeline_paths import get_pipeline_paths
from policy_config import load_policy_config as load_merged_policy_config

_RUNTIME_PATHS = get_pipeline_paths()
PIPELINE_DIR = str(_RUNTIME_PATHS.pipeline_dir)
STATE_DIR = str(_RUNTIME_PATHS.state_dir)
CONFIG_DIR = str(_RUNTIME_PATHS.config_dir)
RESULT_JSON = str(_RUNTIME_PATHS.outputs_dir / "latest_screening_result.json")
REPORT_MD = str(_RUNTIME_PATHS.outputs_dir / "latest_screening_report.md")
FOLDER_MAPPING_PATH = os.path.join(CONFIG_DIR, "feishu_folder_mapping.json")
FEISHU_CONFIG = os.path.join(CONFIG_DIR, "feishu_config.json")
POLICY_CONFIG = os.path.join(CONFIG_DIR, "policy.yaml")
DELIVERY_LOG = os.path.join(STATE_DIR, "delivery_log.jsonl")
TZ_SH = timezone(timedelta(hours=8))

# ── 凭证读取（优先 feishu_config.json，其次 openclaw.json）─────────────
def load_feishu_credentials() -> tuple:
    """Return credentials from environment, local config, or OpenClaw config."""
    env_app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    env_app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if env_app_id and env_app_secret:
        return env_app_id, env_app_secret
    # 优先从 feishu_config.json 读
    if os.path.exists(FEISHU_CONFIG):
        with open(FEISHU_CONFIG) as f:
            cfg = json.load(f)
        app_id = cfg.get("app_id", "").strip()
        app_secret = cfg.get("app_secret", "").strip()
        if app_id and app_secret:
            return app_id, app_secret
    # Fallback 到 openclaw.json
    try:
        with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
            cfg2 = json.load(f)
        feishu_cfg = cfg2.get("channels", {}).get("feishu", {})
        return feishu_cfg.get("appId", ""), feishu_cfg.get("appSecret", "")
    except Exception:
        return "", ""


# ── 飞书 API ──────────────────────────────────────────────────────────
def api_post(url: str, payload: dict, token: str = "") -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.load(resp)
    if result.get("code") != 0:
        raise Exception(f"API error: {result}")
    return result.get("data", {})


def get_tenant_token() -> str:
    app_id, app_secret = load_feishu_credentials()
    if not app_id or not app_secret:
        raise Exception("[deliver] ERROR: feishu_config.json 或 openclaw.json 中未配置 app_id / app_secret")
    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.load(resp)
    if result.get("code") != 0:
        raise Exception(f"Auth failed: {result}")
    token = result.get("tenant_access_token", "")
    if not token:
        raise Exception(f"Auth response missing tenant_access_token: {result}")
    return token


def create_doc(title: str, folder_token: str, token: str) -> str:
    """创建飞书文档，返回 document_id"""
    url = "https://open.feishu.cn/open-apis/docx/v1/documents"
    data = api_post(url, {"title": title, "folder_token": folder_token}, token)
    return data["document"]["document_id"]


def insert_blocks(doc_token: str, token: str, blocks: list, index: int = 0) -> bool:
    """向文档插入 blocks（根级插入：block_id = document_id），自动分批，每批最多50个block"""
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/blocks/{doc_token}/children"
    batch_size = 50
    total_inserted = 0
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i+batch_size]
        payload = {"children": batch, "index": index + i}
        try:
            data = api_post(url, payload, token)
            returned_children = data.get("children") if isinstance(data, dict) else None
            inserted = len(returned_children) if isinstance(returned_children, list) else len(batch)
            total_inserted += inserted
        except Exception as e:
            print(f"[insert_blocks] batch {i//batch_size} error: {e}")
            raise
    print(f"[insert_blocks] Done: {total_inserted} blocks inserted in {(len(blocks)+batch_size-1)//batch_size} batches")
    return True


# ── delivery_log ──────────────────────────────────────────────────────
def log_delivery(event: str, meta: dict):
    os.makedirs(os.path.dirname(DELIVERY_LOG), exist_ok=True)
    record = {"timestamp": datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **meta}
    with open(DELIVERY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def is_successfully_delivered(delivery_meta: dict) -> bool:
    return (
        isinstance(delivery_meta, dict)
        and delivery_meta.get("delivery_status") == "success"
        and bool(delivery_meta.get("feishu_doc_id"))
        and bool(delivery_meta.get("feishu_doc_url"))
    )


def validate_result_data(result_data: dict):
    required = ["run_id", "week_id", "window_start", "window_end"]
    missing = [k for k in required if not result_data.get(k)]
    if missing:
        raise ValueError(f"[deliver] ERROR: screening result missing required fields: {missing}")


def load_policy_config(path: str = POLICY_CONFIG) -> dict:
    return load_merged_policy_config(path)


def build_doc_url(folder_url: str, doc_token: str) -> str:
    subdomain = ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(folder_url)
        subdomain = parsed.netloc.split(".")[0]
    except Exception:
        pass
    feishu_host = f"{subdomain}.feishu.cn" if subdomain else "feishu.cn"
    return f"https://{feishu_host}/docx/{doc_token}"


# ── 构建文档内容 blocks ────────────────────────────────────────────────
def build_blocks(result_data: dict, report_md: str, policy: dict = None) -> list:
    show_notes_translation_enabled, show_notes_translation_options = build_show_notes_translation_render_options(policy)
    return renderer_build_feishu_blocks(
        result_data,
        report_md,
        show_notes_translation_enabled=show_notes_translation_enabled,
        show_notes_translation_options=show_notes_translation_options,
    )


# ── 主流程 ────────────────────────────────────────────────────────────
def main(argv=None):
    parser = argparse.ArgumentParser(description="Deliver weekly podcast report to Feishu")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate inputs and render blocks without calling Feishu APIs or writing state")
    parser.add_argument("--force", action="store_true",
                        help="Create a new Feishu doc even when successful delivery_meta already exists")
    args = parser.parse_args(argv)

    print("[deliver] STARTED")

    # 1. 检查输入文件
    if not os.path.exists(RESULT_JSON):
        print(f"[deliver] ERROR: {RESULT_JSON} not found")
        sys.exit(1)
    if not os.path.exists(REPORT_MD):
        print(f"[deliver] ERROR: {REPORT_MD} not found")
        sys.exit(1)

    result_data = read_latest_result(RESULT_JSON)
    with open(REPORT_MD) as f:
        report_md = f.read()

    run_id = result_data.get("run_id", "unknown")
    week_id = result_data.get("week_id", "unknown")
    try:
        validate_result_data(result_data)
    except Exception as e:
        print(str(e))
        sys.exit(1)

    delivery_meta = result_data.get("delivery_meta", {}) or {}
    already_delivered = is_successfully_delivered(delivery_meta)

    if already_delivered and not args.force and not args.dry_run:
        doc_url = delivery_meta.get("feishu_doc_url", "")
        print(f"[deliver] SKIP: already delivered run_id={run_id} doc_url={doc_url}")
        return doc_url

    if delivery_meta and not already_delivered and not args.force and not args.dry_run:
        status = delivery_meta.get("delivery_status", "unknown")
        print(f"[deliver] ERROR: existing non-success delivery_meta status={status}; use --force to create a new document")
        sys.exit(1)

    # 2. 读取飞书目录映射
    folder_mapping = {}
    if os.path.exists(FOLDER_MAPPING_PATH):
        with open(FOLDER_MAPPING_PATH) as f:
            folder_mapping = json.load(f)

    weekly_reports_folder = folder_mapping.get("weekly_reports", {})
    folder_id = str(weekly_reports_folder.get("feishu_folder_id", ""))
    folder_url = str(weekly_reports_folder.get("feishu_folder_url", ""))

    doc_title = f"🎧 播客周报 {week_id} | {result_data.get('window_start', '')[:10]} ~ {result_data.get('window_end', '')[:10]}"

    if not folder_id:
        err_msg = "[deliver] ERROR: feishu_folder_mapping.json 中 weekly_reports.feishu_folder_id 为空，不允许猜目录"
        print(err_msg)
        if args.dry_run:
            sys.exit(1)
        log_delivery("feishu_doc_create", {
            "run_id": run_id, "week_id": week_id,
            "status": "error_no_folder_mapping",
            "error": err_msg
        })
        sys.exit(1)

    policy = load_policy_config()
    blocks = build_blocks(result_data, report_md, policy=policy)
    if not blocks:
        print("[deliver] ERROR: build_blocks returned empty block list")
        sys.exit(1)

    if args.dry_run:
        if already_delivered and not args.force:
            action = "skip"
        elif already_delivered and args.force:
            action = "force-create"
        elif delivery_meta and not args.force:
            action = "requires-force"
        else:
            action = "create"
        print("[deliver] DRY-RUN")
        print(f"[deliver] run_id={run_id}")
        print(f"[deliver] week_id={week_id}")
        print(f"[deliver] folder_id={folder_id}")
        print(f"[deliver] doc_title={doc_title}")
        print(f"[deliver] blocks_count={len(blocks)}")
        print(f"[deliver] has_delivery_meta={bool(delivery_meta)}")
        print(f"[deliver] action={action}")
        return "dry-run"

    old_doc_id = delivery_meta.get("feishu_doc_id", "") if delivery_meta else ""
    old_doc_url = delivery_meta.get("feishu_doc_url", "") if delivery_meta else ""

    # 3. 获取 token
    try:
        token = get_tenant_token()
    except Exception as e:
        print(f"[deliver] ERROR getting token: {e}")
        log_delivery("feishu_doc_create", {
            "run_id": run_id, "week_id": week_id,
            "status": "error_token",
            "error": str(e)
        })
        sys.exit(1)

    # 4. 创建文档
    try:
        doc_token = create_doc(doc_title, folder_id, token)
    except Exception as e:
        print(f"[deliver] ERROR creating doc: {e}")
        log_delivery("feishu_doc_create", {
            "run_id": run_id, "week_id": week_id,
            "status": "error_create",
            "error": str(e)
        })
        sys.exit(1)

    # 构建正确的 doc URL（使用与 folder 相同的 subdomain，避免 404）
    doc_url = build_doc_url(folder_url, doc_token)
    print(f"[deliver] Doc created: {doc_url}")

    # 5. 写入 blocks
    try:
        insert_blocks(doc_token, token, blocks)
    except Exception as e:
        print(f"[deliver] ERROR inserting blocks: {e}")
        attempted_at = datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z")
        store_write_delivery_meta(RESULT_JSON, result_data, {
            "feishu_doc_id": doc_token,
            "feishu_doc_url": doc_url,
            "feishu_weekly_folder_id": folder_id,
            "feishu_weekly_folder_url": folder_url,
            "delivery_status": "error_write_blocks",
            "error": str(e),
            "attempted_at": attempted_at
        })
        log_delivery("feishu_doc_create", {
            "run_id": run_id, "week_id": week_id,
            "status": "error_write_blocks",
            "doc_token": doc_token, "doc_url": doc_url,
            "error": str(e),
            "forced": bool(args.force),
            "old_doc_id": old_doc_id,
            "old_doc_url": old_doc_url,
            "new_doc_id": doc_token,
            "new_doc_url": doc_url
        })
        sys.exit(1)

    print(f"[deliver] Blocks written: {len(blocks)} blocks")

    # 6. 回写 delivery_meta 到 result JSON
    delivery_meta = {
        "feishu_doc_id": doc_token,
        "feishu_doc_url": doc_url,
        "feishu_weekly_folder_id": folder_id,
        "feishu_weekly_folder_url": folder_url,
        "delivery_status": "success",
        "delivered_at": datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z")
    }
    store_write_delivery_meta(RESULT_JSON, result_data, delivery_meta)

    # 7. delivery log
    log_delivery("feishu_doc_create", {
        "run_id": run_id,
        "week_id": week_id,
        "status": "success",
        "doc_token": doc_token,
        "doc_url": doc_url,
        "folder_id": folder_id,
        "blocks_count": len(blocks),
        "forced": bool(args.force),
        "old_doc_id": old_doc_id,
        "old_doc_url": old_doc_url,
        "new_doc_id": doc_token,
        "new_doc_url": doc_url
    })

    print(f"[deliver] DONE: {doc_url}")
    return doc_url


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[deliver] FATAL: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
