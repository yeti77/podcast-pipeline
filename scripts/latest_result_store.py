#!/usr/bin/env python3
"""
Small helpers for latest screening result state.

The write helpers intentionally use plain open(path, "w") so writes to
outputs/latest_screening_result.json follow the symlink to the real run JSON.
Do not replace/rename the latest symlink here.
"""

import json
import os


def read_result(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_result_follow_symlink(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_delivery_meta(path: str, result_data: dict, delivery_meta: dict) -> None:
    result_data["delivery_meta"] = delivery_meta
    write_result_follow_symlink(path, result_data)


def write_notification_meta(path: str, result_data: dict, notification_meta: dict) -> None:
    result_data["notification_meta"] = notification_meta
    write_result_follow_symlink(path, result_data)


def update_latest_pointers(outputs_dir: str, run_dir: str, result_path: str, report_path: str) -> None:
    outputs_dir = os.path.abspath(outputs_dir)
    run_dir = os.path.abspath(run_dir)
    result_path = os.path.abspath(result_path)
    report_path = os.path.abspath(report_path)

    if os.path.commonpath([run_dir, result_path]) != run_dir:
        raise ValueError(
            f"[latest_result_store] result_path must be inside run_dir: "
            f"result_path={result_path} run_dir={run_dir}"
        )
    if os.path.commonpath([run_dir, report_path]) != run_dir:
        raise ValueError(
            f"[latest_result_store] report_path must be inside run_dir: "
            f"report_path={report_path} run_dir={run_dir}"
        )
    if os.path.basename(result_path) != "screening_result.json":
        raise ValueError(
            f"[latest_result_store] result_path must be named screening_result.json: "
            f"result_path={result_path}"
        )
    if os.path.basename(report_path) != "screening_report.md":
        raise ValueError(
            f"[latest_result_store] report_path must be named screening_report.md: "
            f"report_path={report_path}"
        )

    if not os.path.exists(result_path):
        raise FileNotFoundError(f"[latest_result_store] result not found: {result_path}")
    if not os.path.exists(report_path):
        raise FileNotFoundError(f"[latest_result_store] report not found: {report_path}")

    result_data = read_result(result_path)
    run_id_from_json = result_data.get("run_id", "")
    week_id_from_json = result_data.get("week_id", "")
    run_dir_name = os.path.basename(run_dir)
    week_dir_name = os.path.basename(os.path.dirname(run_dir))

    if run_id_from_json != run_dir_name:
        raise ValueError(
            f"[latest_result_store] run_id mismatch: JSON={run_id_from_json} dir={run_dir_name}"
        )
    if week_id_from_json != week_dir_name:
        raise ValueError(
            f"[latest_result_store] week_id mismatch: JSON={week_id_from_json} dir={week_dir_name}"
        )

    os.makedirs(outputs_dir, exist_ok=True)
    for basename in ["latest_screening_result.json", "latest_screening_report.md"]:
        latest_link = os.path.join(outputs_dir, basename)
        if os.path.islink(latest_link) or os.path.exists(latest_link):
            os.remove(latest_link)

    result_link = os.path.join(outputs_dir, "latest_screening_result.json")
    report_link = os.path.join(outputs_dir, "latest_screening_report.md")
    rel = os.path.relpath(run_dir, outputs_dir)

    os.symlink(f"{rel}/screening_result.json", result_link)
    os.symlink(f"{rel}/screening_report.md", report_link)

    result_target_dir = os.path.dirname(os.path.join(outputs_dir, os.readlink(result_link)))
    report_target_dir = os.path.dirname(os.path.join(outputs_dir, os.readlink(report_link)))
    if result_target_dir != report_target_dir:
        raise RuntimeError(
            f"[latest_result_store] inconsistent latest pointers: "
            f"result={os.readlink(result_link)} report={os.readlink(report_link)}"
        )
