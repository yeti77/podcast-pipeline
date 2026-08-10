#!/usr/bin/env python3
"""
process_selection_queue.py v2.3
处理 selection_queue.jsonl 中 status=queued 的任务：
  1. 根据 podcast_id + action 找到飞书目录
  2. 生成 preview 或 full 文档内容
  3. 写入飞书文档
  4. 更新 queue 状态 + processing_log + episode processing_status
用法：python3 process_selection_queue.py [--dry-run]
"""

import sys
import os
import json
import urllib.request
import urllib.error
import glob
import argparse
from datetime import datetime, timezone, timedelta
from pipeline_paths import get_pipeline_paths

_RUNTIME_PATHS = get_pipeline_paths()
PIPELINE_DIR = str(_RUNTIME_PATHS.pipeline_dir)
STATE_DIR = str(_RUNTIME_PATHS.state_dir)
CONFIG_DIR = str(_RUNTIME_PATHS.config_dir)
QUEUE_FILE = os.path.join(STATE_DIR, "selection_queue.jsonl")
PROCESSING_LOG = os.path.join(STATE_DIR, "processing_log.jsonl")
FOLDER_MAPPING = os.path.join(CONFIG_DIR, "feishu_folder_mapping.json")
FEISHU_CONFIG = os.path.join(CONFIG_DIR, "feishu_config.json")
REGISTRY_FILE = os.path.join(STATE_DIR, "episode_registry.jsonl")
TZ_SH = timezone(timedelta(hours=8))


# ── 凭证读取 ──────────────────────────────────────────────────────────
def load_feishu_credentials() -> tuple:
    env_app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    env_app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if env_app_id and env_app_secret:
        return env_app_id, env_app_secret
    if os.path.exists(FEISHU_CONFIG):
        with open(FEISHU_CONFIG) as f:
            cfg = json.load(f)
        app_id = cfg.get("app_id", "").strip()
        app_secret = cfg.get("app_secret", "").strip()
        if app_id and app_secret:
            return app_id, app_secret
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
        raise Exception("ERROR: feishu_config.json 或 openclaw.json 中未配置 app_id / app_secret")

    payload = json.dumps({
        "app_id": app_id,
        "app_secret": app_secret
    }).encode()

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
    url = "https://open.feishu.cn/open-apis/docx/v1/documents"
    result = api_post(url, {"title": title, "folder_token": folder_token}, token)
    return result["document"]["document_id"]


def insert_blocks(doc_token: str, token: str, blocks: list) -> bool:
    # 飞书 blocks API: /documents/{document_id}/blocks/{block_id}/children
    # 根级插入时 block_id = document_id
    url = (f"https://open.feishu.cn/open-apis/docx/v1/documents"
           f"/{doc_token}/blocks/{doc_token}/children")
    api_post(url, {"children": blocks, "index": 0}, token)
    return True


# ── Feishu block builders ─────────────────────────────────────────────
def h1(text: str) -> dict:
    return {"block_type": 3, "heading1": {"elements": [text_run(text)], "property": {}}}

def h2(text: str) -> dict:
    return {"block_type": 4, "heading2": {"elements": [text_run(text)], "property": {}}}

def para(text: str, bold: bool = False) -> dict:
    style = {"bold": True} if bold else {}
    return {"block_type": 2, "text": {"elements": [text_run(text, style)], "property": {}}}

def bullet(text: str) -> dict:
    return {"block_type": 12, "bullet": {"elements": [text_run(text)], "property": {"indent_level": 1}}}

def divider() -> dict:
    return None  # block_type=25 (divider) 不支持 API 创建，跳过

def text_run(text: str, style: dict = None) -> dict:
    r = {"content": text}
    if style:
        r["text_element_style"] = style
    return {"type": "text_run", "text_run": r}


# ── 构建 selection 文档 blocks ────────────────────────────────────────
def build_selection_doc_blocks(episode: dict, action: str) -> list:
    """为一条 selection 生成飞书文档内容"""
    action_label = "Full 推荐转写" if action == "full" else "Preview 预览"
    action_tag = "[Full]" if action == "full" else "[Preview]"
    publish_date = episode.get("publish_date", "")
    podcast_name = episode.get("podcast_name", "")
    episode_title = episode.get("episode_title", "")
    doc_title = f"{action_tag} {publish_date} {podcast_name} - {episode_title}"

    blocks = []
    blocks.append(h1(f"{action_tag} {episode_title}"))
    blocks.append(para(""))
    blocks.append(para(f"播客：{podcast_name}", bold=True))
    blocks.append(para(f"发布：{publish_date}"))
    blocks.append(para(f"评分：{episode.get('score', 0)}分"))
    blocks.append(para(f"推荐动作：{action_label}"))
    if episode.get("duration_minutes"):
        blocks.append(para(f"时长：{episode.get('duration_minutes')}分钟"))
    if episode.get("audio_url"):
        blocks.append(para(f"原始音频：{episode.get('audio_url')}"))
    blocks.append(divider())

    # 摘要
    blocks.append(h2("📝 内容摘要"))
    summary = episode.get("summary_3_sentences_cn", [])
    if summary:
        for s in summary:
            blocks.append(bullet(s))
    else:
        blocks.append(bullet("（暂无摘要，等待转写后补充）"))
    blocks.append(para(""))

    # 关键要点
    blocks.append(h2("🔑 关键要点"))
    key_pts = episode.get("key_points_cn", [])
    if key_pts:
        for pt in key_pts:
            blocks.append(bullet(pt))
    else:
        blocks.append(bullet("（暂无要点，等待转写后补充）"))
    blocks.append(para(""))

    # 重要原因
    blocks.append(h2("💡 推荐理由"))
    blocks.append(para(episode.get("why_important", episode.get("reason", ""))))
    blocks.append(para(""))

    # 清洗后的 show_notes
    notes = episode.get("show_notes_text", "")
    if notes:
        blocks.append(divider())
        blocks.append(h2("📋 原始 show notes（已清洗）"))
        for line in (notes[:1000].split("\n") if "\n" in notes else [notes[:500]]):
            if line.strip():
                blocks.append(para(line.strip()))
    blocks.append(para(""))
    blocks.append(divider())
    blocks.append(para(f"selection_id：{episode.get('selection_id', '')}"))
    blocks.append(para(f"episode_id：{episode.get('episode_id', '')}"))
    blocks.append(para(f"入队时间：{episode.get('selected_at', '')}"))

    # 过滤掉 None（divider 等不可创建的 block type）
    blocks = [b for b in blocks if b is not None]
    return blocks, doc_title


# ── 目录查找 ─────────────────────────────────────────────────────────
def find_folder(podcast_id: str, action: str, folder_mapping: dict) -> tuple:
    """
    返回 (folder_id, folder_url)。
    只查找 podcasts.{podcast_id}.{action}_folder_id，不做 weekly_reports fallback。
    若未配置，返回空字符串，由调用方显式报错。
    """
    podcasts = folder_mapping.get("podcasts", {})
    pod_cfg = podcasts.get(podcast_id, {})
    fid = pod_cfg.get(f"{action}_folder_id", "")
    furl = pod_cfg.get(f"{action}_folder_url", "")
    return fid, furl


# ── Processing log ────────────────────────────────────────────────────
def log_processing(selection_id: str, episode_id: str, action: str,
                   status: str, started_at: str, finished_at: str,
                   feishu_doc_url: str, feishu_doc_id: str, error: str = ""):
    record = {
        "timestamp": finished_at,
        "selection_id": selection_id,
        "episode_id": episode_id,
        "action": action,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "feishu_doc_url": feishu_doc_url,
        "feishu_doc_id": feishu_doc_id,
        "error": error
    }
    with open(PROCESSING_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Update queue entry ────────────────────────────────────────────────
def update_queue_status(selection_id: str, new_status: str,
                        feishu_doc_id: str = "", feishu_doc_url: str = "",
                        finished_at: str = ""):
    """重写 queue（只保留最新一条 selection_id）"""
    records = []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except:
                continue
            if r.get("selection_id") == selection_id:
                r["status"] = new_status
                if feishu_doc_id:
                    r["feishu_doc_id"] = feishu_doc_id
                if feishu_doc_url:
                    r["feishu_doc_url"] = feishu_doc_url
                if finished_at:
                    r["finished_at"] = finished_at
            records.append(r)
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ── Update registry processing_status ─────────────────────────────────
def update_registry_processing_status(episode_id: str, action: str,
                                      queue_status: str,
                                      preview_doc_url: str = "",
                                      full_doc_url: str = "",
                                      finished_at: str = ""):
    """更新 episode_registry.jsonl 中对应记录的 processing_status"""
    records = {}
    with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                records[r.get("episode_id", "")] = r
            except:
                continue

    if episode_id not in records:
        return

    reg = records[episode_id]
    ps = reg.get("processing_status", {})
    ps["selected_action"] = action
    ps["queue_status"] = queue_status
    if preview_doc_url:
        ps["preview_doc_url"] = preview_doc_url
    if full_doc_url:
        ps["full_doc_url"] = full_doc_url
    if finished_at:
        ps["last_processed_at"] = finished_at

    reg["processing_status"] = ps
    records[episode_id] = reg

    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        for r in records.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ── 主处理流程 ────────────────────────────────────────────────────────
def process_one(entry: dict, token: str, dry_run: bool = False) -> dict:
    selection_id = entry["selection_id"]
    episode_id = entry["episode_id"]
    action = entry["action"]  # "preview" or "full"
    episode = entry.get("episode", {})

    started_at = datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z")
    feishu_doc_id = ""
    feishu_doc_url = ""
    error = ""

    try:
        # 加载目录映射
        with open(FOLDER_MAPPING) as f:
            folder_mapping = json.load(f)

        # 查找目录
        podcast_id = episode.get("podcast_id") or entry.get("podcast_id", "")
        folder_id, folder_url = find_folder(podcast_id, action, folder_mapping)

        if not folder_id:
            raise Exception(
                f"未找到 podcast_id={podcast_id} action={action} 对应的飞书目录。"
                "请在 config/feishu_folder_mapping.json 中配置 podcasts.{podcast_id}.{action}_folder_id"
            )

        # 注入 queue 元信息到 episode（方便文档里显示）
        episode["selection_id"] = selection_id
        episode["selected_at"] = entry.get("selected_at", "")

        # 构建文档
        blocks, doc_title = build_selection_doc_blocks(episode, action)

        if dry_run:
            print(f"[dry-run] Would create doc: {doc_title}")
            print(f"[dry-run] In folder: {folder_id}")
            return {"status": "dry_run", "doc_title": doc_title}

        # 写入飞书
        feishu_doc_id = create_doc(doc_title, folder_id, token)
        feishu_doc_url = f"https://feishu.cn/document/{feishu_doc_id}"
        insert_blocks(feishu_doc_id, token, blocks)
        print(f"[process] Created: {feishu_doc_url}")

    except Exception as e:
        error = str(e)
        print(f"[process] ERROR {selection_id}: {e}")

    finished_at = datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z")

    # 写 processing log
    log_processing(
        selection_id, episode_id, action,
        "success" if not error else "error",
        started_at, finished_at,
        feishu_doc_url, feishu_doc_id, error
    )

    # 更新 queue 状态
    new_status = "done" if not error else "error"
    update_queue_status(selection_id, new_status,
                        feishu_doc_id, feishu_doc_url, finished_at)

    # 更新 registry processing_status
    preview_url = feishu_doc_url if action == "preview" else ""
    full_url = feishu_doc_url if action == "full" else ""
    update_registry_processing_status(
        episode_id, action, new_status,
        preview_url, full_url, finished_at
    )

    return {"status": new_status, "doc_url": feishu_doc_url, "error": error}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印不实际写入飞书")
    parser.add_argument("--selection-id", dest="selection_id", default=None,
                        help="只处理指定 selection_id")
    args = parser.parse_args()

    print(f"[process] STARTED dry_run={args.dry_run}")

    # 加载 queue
    if not os.path.exists(QUEUE_FILE):
        print("[process] QUEUE_FILE not found, nothing to do")
        sys.exit(0)

    queued = []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("status") == "queued":
                    if args.selection_id is None or r.get("selection_id") == args.selection_id:
                        queued.append(r)
            except json.JSONDecodeError:
                continue

    if not queued:
        print("[process] No queued items to process")
        sys.exit(0)

    print(f"[process] Found {len(queued)} queued item(s)")

    # dry-run 模式下跳过 token 获取和所有写操作
    if args.dry_run:
        print("[process] === DRY-RUN MODE (no API calls, no file writes) ===")
        folder_mapping = {}
        if os.path.exists(FOLDER_MAPPING):
            with open(FOLDER_MAPPING) as f:
                folder_mapping = json.load(f)
        for entry in queued:
            episode = entry.get("episode", {})
            podcast_id = episode.get("podcast_id") or entry.get("podcast_id", "")
            action = entry.get("action", "")
            folder_id, folder_url = find_folder(podcast_id, action, folder_mapping)
            blocks, doc_title = build_selection_doc_blocks(episode, action)
            print(f"[dry-run] {entry['selection_id']} | action={action} | podcast={podcast_id}")
            print(f"[dry-run]   → folder_id={folder_id or '(未配置)'}")
            print(f"[dry-run]   → title={doc_title}")
            print(f"[dry-run]   → episode_id={entry['episode_id'][:40]}")
        print("[process] DRY-RUN DONE (no changes made)")
        sys.exit(0)

    token = get_tenant_token()

    for entry in queued:
        sid = entry["selection_id"]
        print(f"[process] Processing {sid}...")
        result = process_one(entry, token, dry_run=args.dry_run)
        if result.get("status") == "success":
            print(f"[process] ✅ {sid} → {result.get('doc_url')}")
        else:
            print(f"[process] ❌ {sid} → {result.get('error')}")

    print("[process] DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[process] FATAL: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
