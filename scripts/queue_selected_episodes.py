#!/usr/bin/env python3
"""
queue_selected_episodes.py v2.4
将用户选中的 episode 写入 selection_queue.jsonl。
支持两种输入方式：
  1. JSON 文件：python3 queue_selected_episodes.py batch episodes.json
  2. 命令行：python3 queue_selected_episodes.py enqueue --episode-id YYY --action full
JSON 格式：
[
  {
    "run_id": "20260421_130000",
    "week_id": "2026W17",
    "episode_id": "A16Z_20260420_rethinking_git_age_8d3fbf21",
    "podcast_id": "A16Z",
    "action": "full",
    "episode": { ... }   // 完整 episode 对象（来自 screening_result.json）
  }
]

v2.4（基于 v2.3）：
  [fix-q1] 新增 --screening-result 验证逻辑：所选 episode 的 publish_datetime 必须落在
           对应 screening_result 的窗口内（window_start <= pub < window_end）
  [fix-q2] 支持 --run-id 查找对应 screening_result.json
  [fix-q3] 移除 --week-id 的隐式假设，改为显式窗口验证
"""

import sys
import os
import json
import argparse
import glob
import hashlib
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from pipeline_paths import get_pipeline_paths

_RUNTIME_PATHS = get_pipeline_paths()
PIPELINE_DIR = str(_RUNTIME_PATHS.pipeline_dir)
STATE_DIR = str(_RUNTIME_PATHS.state_dir)
OUTPUT_DIR = str(_RUNTIME_PATHS.outputs_dir)
QUEUE_FILE = os.path.join(STATE_DIR, "selection_queue.jsonl")
TZ_SH = ZoneInfo("Asia/Shanghai")


def make_selection_id(episode_id: str, action: str) -> str:
    ts = datetime.now(TZ_SH).strftime("%Y%m%d%H%M%S")
    h = hashlib.sha1(f"{episode_id}:{action}:{ts}".encode()).hexdigest()[:6]
    return f"sel_{ts}_{h}"


def find_screening_result(run_id: str = None, screening_result_path: str = None) -> dict:
    """查找并加载 screening_result.json"""
    if screening_result_path:
        path = screening_result_path
    elif run_id:
        # 在 outputs/runs/*/<run_id>/screening_result.json 中查找
        pattern = os.path.join(OUTPUT_DIR, "runs", "*", run_id, "screening_result.json")
        files = glob.glob(pattern)
        if not files:
            raise FileNotFoundError(f"No screening_result.json found for run_id={run_id}")
        path = files[0]
    else:
        # fallback 到 latest
        path = os.path.join(OUTPUT_DIR, "latest_screening_result.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Screening result not found: {path}")
    with open(path) as f:
        return json.load(f)


def validate_episode_in_window(episode: dict, screening_result: dict) -> bool:
    """
    验证 episode 的 publish_datetime 是否落在 screening_result 的窗口内。
    规则：published_at >= window_start AND published_at < window_end
    """
    pub_dt_str = episode.get("pub_datetime") or episode.get("publish_at") or ""
    if not pub_dt_str:
        return False

    try:
        pub_dt = parsedate_to_datetime(pub_dt_str)
    except Exception:
        try:
            normalized = pub_dt_str.replace("Z", "+00:00").replace("+0000", "+00:00")
            pub_dt = datetime.fromisoformat(normalized)
        except Exception:
            return False

    ws_str = screening_result.get("window_start", "")
    we_str = screening_result.get("window_end", "")
    if not ws_str or not we_str:
        return False

    try:
        ws = parsedate_to_datetime(ws_str)
        we = parsedate_to_datetime(we_str)
    except Exception:
        return False

    pub_utc = pub_dt.astimezone(timezone.utc)
    ws_utc = ws.astimezone(timezone.utc)
    we_utc = we.astimezone(timezone.utc)
    return ws_utc <= pub_utc < we_utc


def enqueue_selection(run_id: str, week_id: str, episode_id: str,
                      podcast_id: str, action: str, episode: dict) -> str:
    selection_id = make_selection_id(episode_id, action)
    record = {
        "selection_id": selection_id,
        "run_id": run_id,
        "week_id": week_id,
        "episode_id": episode_id,
        "podcast_id": podcast_id,
        "action": action,
        "episode": episode,
        "selected_at": datetime.now(TZ_SH).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "queued"
    }
    with open(QUEUE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return selection_id


def load_queue() -> list:
    if not os.path.exists(QUEUE_FILE):
        return []
    records = []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def cmd_enqueue(args, episode: dict):
    """从命令行参数入队，episode 通过显式参数传入"""
    # [fix-q1] 窗口验证：如果提供了 --screening-result 或 --run-id，必须验证 episode 落在窗口内
    screening_result = None
    if args.screening_result or args.run_id_for_episode or args.run_id != "manual":
        try:
            screening_result = find_screening_result(
                run_id=args.run_id if args.run_id != "manual" else None,
                screening_result_path=args.screening_result
            )
        except (FileNotFoundError, Exception) as e:
            print(f"[queue] ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    if screening_result:
        if not validate_episode_in_window(episode, screening_result):
            pub_dt = episode.get("pub_datetime") or episode.get("publish_at", "")
            ws = screening_result.get("window_start", "")
            we = screening_result.get("window_end", "")
            wid = screening_result.get("week_id", "unknown")
            print(
                f"[queue] ERROR: episode publish_datetime={pub_dt} "
                f"does not fall in window for {wid} "
                f"(window_start={ws}, window_end={we}). "
                f"episode_id={args.episode_id[:40]}. FAILING.",
                file=sys.stderr
            )
            sys.exit(1)
        print(f"[queue] Window validation passed: {screening_result.get('week_id')} | "
              f"{screening_result.get('window_start')} ~ {screening_result.get('window_end')}")

    selection_id = enqueue_selection(
        run_id=args.run_id or "manual",
        week_id=args.week_id or "manual",
        episode_id=args.episode_id,
        podcast_id=args.podcast_id or "",
        action=args.action,
        episode=episode
    )
    print(f"[queue] ENQUEUED selection_id={selection_id} episode_id={args.episode_id} action={args.action}")
    return selection_id


def json_enqueue(filepath: str) -> list:
    """从 JSON 文件批量入队"""
    with open(filepath) as f:
        items = json.load(f)
    selection_ids = []
    for item in items:
        sid = enqueue_selection(
            run_id=item.get("run_id", "manual"),
            week_id=item.get("week_id", "manual"),
            episode_id=item["episode_id"],
            podcast_id=item.get("podcast_id", ""),
            action=item["action"],
            episode=item.get("episode", {})
        )
        selection_ids.append(sid)
    print(f"[queue] BATCH ENQUEUED {len(selection_ids)} items")
    return selection_ids


def cmd_list(args):
    """列出当前队列"""
    records = load_queue()
    if not records:
        print("[queue] 空队列")
        return
    print(f"[queue] 共 {len(records)} 条记录：")
    for r in records:
        print(f"  [{r['status']}] {r['selection_id']} | {r['episode_id'][:40]} | action={r['action']} | selected_at={r['selected_at']}")


def cmd_status(args):
    """查单条状态"""
    records = load_queue()
    for r in records:
        if args.selection_id and r["selection_id"] == args.selection_id:
            print(json.dumps(r, indent=2, ensure_ascii=False))
            return
    print(f"[queue] 未找到 selection_id={args.selection_id}")


def main():
    parser = argparse.ArgumentParser(description="播客 selection 入队工具")
    sub = parser.add_subparsers(dest="cmd")

    # enqueue 子命令
    enq = sub.add_parser("enqueue", help="单条入队")
    # [fix-q1] enqueue 子命令新增窗口验证参数
    enq.add_argument("--run-id", dest="run_id", default="manual",
                      help="Run ID，用于查找对应的 screening_result.json")
    enq.add_argument("--screening-result", dest="screening_result", default=None,
                      help="显式指定 screening_result.json 路径，用于验证 episode 落在对应窗口内")
    enq.add_argument("--week-id", dest="week_id", default="manual",
                      help="业务周 ID（已废弃，仅作记录用）")
    enq.add_argument("--episode-id", required=True,
                      help="Episode ID")
    enq.add_argument("--podcast-id", dest="podcast_id", default="",
                      help="播客 ID")
    enq.add_argument("--action", required=True, choices=["preview", "full"],
                      help="Full 或 Preview")
    enq.add_argument("--episode-json", dest="episode_json", default=None,
                      help="Episode 完整对象的 JSON 文件路径")
    enq.add_argument("--run-id-for-episode", dest="run_id_for_episode", default=None,
                      help="从指定 run_id 的 screening_result.json 中读取 episode 数据（已被 --screening-result 替代）")

    # batch 子命令
    batch = sub.add_parser("batch", help="从 JSON 文件批量入队")
    batch.add_argument("json_file", help="批量 JSON 文件路径")

    # list 子命令
    sub.add_parser("list", help="列出当前队列")

    # status 子命令
    stat = sub.add_parser("status", help="查单条状态")
    stat.add_argument("selection_id", help="selection_id")

    args = parser.parse_args()

    if args.cmd == "enqueue":
        episode = {}
        if args.episode_json:
            with open(args.episode_json) as f:
                episode = json.load(f)
        elif args.run_id_for_episode:
            ep_id = args.episode_id
            result_file = os.path.join(OUTPUT_DIR, "runs",
                                       "*", args.run_id_for_episode,
                                       "screening_result.json")
            import glob
            files = glob.glob(result_file)
            if files:
                with open(files[0]) as f:
                    d = json.load(f)
                    for ep in d.get("full", []) + d.get("preview", []) + d.get("skip", []):
                        if ep.get("episode_id") == ep_id:
                            episode = ep
                            break
        cmd_enqueue(args, episode)

    elif args.cmd == "batch":
        json_enqueue(args.json_file)

    elif args.cmd == "list":
        cmd_list(args)

    elif args.cmd == "status":
        cmd_status(args)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
