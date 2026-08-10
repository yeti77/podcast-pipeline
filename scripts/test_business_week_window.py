#!/usr/bin/env python3
"""
test_business_week_window.py
业务周窗口算法测试套件（v2.2）

运行：
  cd ~/podcast_pipeline/scripts
  python3 test_business_week_window.py
"""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from podcast_screener import (
    get_business_week_window,
    business_week_id,
    validate_week_id,
    episode_in_window,
)
from email.utils import parsedate_to_datetime
from datetime import timezone

TZ_SH = ZoneInfo("Asia/Shanghai")


class TestBusinessWeekWindow(unittest.TestCase):
    """业务周窗口算法测试（v2.2）"""

    def t(self, now_str, expected_wid, expected_ws, expected_we):
        """通用测试断言"""
        now = datetime.fromisoformat(now_str).replace(tzinfo=TZ_SH)
        ws, we, wid = get_business_week_window(now)
        norm = lambda s: s.replace("+0800", "+08:00")
        ws_str = norm(ws.strftime("%Y-%m-%dT%H:%M:%S%z"))
        we_str = norm(we.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self.assertEqual(wid, expected_wid,
            f"[{now_str}] week_id: got {wid} != expected {expected_wid}")
        self.assertEqual(ws_str, norm(expected_ws),
            f"[{now_str}] window_start: got {ws_str} != expected {norm(expected_ws)}")
        self.assertEqual(we_str, norm(expected_we),
            f"[{now_str}] window_end: got {we_str} != expected {norm(expected_we)}")
        self.assertTrue(validate_week_id(ws, wid),
            f"[{now_str}] self-validate failed: ws={ws_str} wid={wid}")

    # ── 用户指定期望值测试 ────────────────────────────────────────────
    def test_may4_morning_w18(self):
        """2026-05-04 09:00 Mon → W18 | Apr 26 22:00 → May 3 22:00"""
        self.t("2026-05-04T09:00:00+08:00",
               "2026W18",
               "2026-04-26T22:00:00+08:00",
               "2026-05-03T22:00:00+08:00")

    def test_may10_215959_w18(self):
        """2026-05-10 21:59:59 Sun → W18（22:00 还没到，等于上一周）"""
        self.t("2026-05-10T21:59:59+08:00",
               "2026W18",
               "2026-04-26T22:00:00+08:00",
               "2026-05-03T22:00:00+08:00")

    def test_may10_220000_w19(self):
        """2026-05-10 22:00:00 Sun → W19（精确边界，属于新一周）"""
        self.t("2026-05-10T22:00:00+08:00",
               "2026W19",
               "2026-05-03T22:00:00+08:00",
               "2026-05-10T22:00:00+08:00")

    def test_may10_232600_w19(self):
        """2026-05-10 23:26 Sun → W19"""
        self.t("2026-05-10T23:26:00+08:00",
               "2026W19",
               "2026-05-03T22:00:00+08:00",
               "2026-05-10T22:00:00+08:00")

    def test_may11_090000_w19(self):
        """2026-05-11 09:00 Mon → W19"""
        self.t("2026-05-11T09:00:00+08:00",
               "2026W19",
               "2026-05-03T22:00:00+08:00",
               "2026-05-10T22:00:00+08:00")

    def test_may17_215959_w19(self):
        """2026-05-17 21:59:59 Sun → W19（22:00 还没到）"""
        self.t("2026-05-17T21:59:59+08:00",
               "2026W19",
               "2026-05-03T22:00:00+08:00",
               "2026-05-10T22:00:00+08:00")

    def test_may17_220000_w20(self):
        """2026-05-17 22:00:00 Sun → W20（精确边界，属于新一周）"""
        self.t("2026-05-17T22:00:00+08:00",
               "2026W20",
               "2026-05-10T22:00:00+08:00",
               "2026-05-17T22:00:00+08:00")

    # ── 自洽性测试 ───────────────────────────────────────────────────
    def test_week_id_self_validate(self):
        """week_id 必须等于 business_week_id(window_start)"""
        test_times = [
            "2026-05-04T09:00:00+08:00",
            "2026-05-10T21:59:59+08:00",
            "2026-05-10T22:00:00+08:00",
            "2026-05-10T23:26:00+08:00",
            "2026-05-11T09:00:00+08:00",
            "2026-05-17T21:59:59+08:00",
            "2026-05-17T22:00:00+08:00",
        ]
        for ts in test_times:
            now = datetime.fromisoformat(ts).replace(tzinfo=TZ_SH)
            ws, _, wid = get_business_week_window(now)
            expected = business_week_id(ws)
            self.assertEqual(wid, expected,
                f"{ts}: wid={wid} expected={expected}")


class TestEpisodeInWindow(unittest.TestCase):
    """episode_in_window 过滤规则测试（开区间）"""

    def window(self):
        ws = datetime.fromisoformat("2026-05-03T22:00:00+08:00").replace(
            tzinfo=ZoneInfo("Asia/Shanghai"))
        we = datetime.fromisoformat("2026-05-10T22:00:00+08:00").replace(
            tzinfo=ZoneInfo("Asia/Shanghai"))
        return ws, we

    def test_before_window(self):
        """pub 在窗口开始前 → False"""
        ws, we = self.window()
        ep = {"pub_datetime": "Sat, 02 May 2026 22:00:00 +0800"}
        self.assertFalse(episode_in_window(ep, ws, we))

    def test_at_window_start(self):
        """pub 在窗口开始精确时刻 → True"""
        ws, we = self.window()
        ep = {"pub_datetime": "Sun, 03 May 2026 22:00:00 +0800"}
        self.assertTrue(episode_in_window(ep, ws, we))

    def test_one_second_after_start(self):
        """pub 在窗口开始后 1 秒 → True"""
        ws, we = self.window()
        ep = {"pub_datetime": "Sun, 03 May 2026 22:00:01 +0800"}
        self.assertTrue(episode_in_window(ep, ws, we))

    def test_one_second_before_end(self):
        """pub 在窗口结束前 1 秒 → True"""
        ws, we = self.window()
        ep = {"pub_datetime": "Sun, 10 May 2026 21:59:58 +0800"}
        self.assertTrue(episode_in_window(ep, ws, we))

    def test_at_window_end_excluded(self):
        """pub 在窗口结束精确时刻 → False（开区间）"""
        ws, we = self.window()
        ep = {"pub_datetime": "Sun, 10 May 2026 22:00:00 +0800"}
        self.assertFalse(episode_in_window(ep, ws, we))

    def test_one_second_after_end(self):
        """pub 在窗口结束后 1 秒 → False"""
        ws, we = self.window()
        ep = {"pub_datetime": "Sun, 10 May 2026 22:00:01 +0800"}
        self.assertFalse(episode_in_window(ep, ws, we))


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestBusinessWeekWindow))
    suite.addTests(loader.loadTestsFromTestCase(TestEpisodeInWindow))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print(f"\n{'='*60}")
    print(f"{'ALL TESTS PASSED' if result.wasSuccessful() else 'SOME TESTS FAILED'}")
    print(f"Total: {result.testsRun} | Failures: {len(result.failures)} | Errors: {len(result.errors)}")
