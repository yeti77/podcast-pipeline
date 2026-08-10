#!/usr/bin/env python3
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from guest_cache_store import (
    get_cache_entry,
    guest_key,
    is_cache_entry_valid,
    load_cache,
    save_cache,
    write_cache_entry,
)


TZ_SH = timezone(timedelta(hours=8))


class TestGuestCacheStore(unittest.TestCase):
    def test_load_missing_cache_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "guest_profiles_cache.json")
            self.assertEqual(load_cache(cache_file), {})

    def test_load_malformed_cache_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "guest_profiles_cache.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write("{not-json")

            self.assertEqual(load_cache(cache_file), {})

    def test_save_cache_writes_utf8_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "guest_profiles_cache.json")
            save_cache(cache_file, {"guest": {"background_zh": "中文背景"}})

            with open(cache_file, encoding="utf-8") as f:
                raw = f.read()
            self.assertIn("中文背景", raw)
            self.assertEqual(json.loads(raw), {"guest": {"background_zh": "中文背景"}})

    def test_save_cache_creates_parent_at_write_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "new-state", "guest_profiles_cache.json")

            save_cache(cache_file, {"guest": {"guest_name": "Freda Duan"}})

            self.assertEqual(load_cache(cache_file)["guest"]["guest_name"], "Freda Duan")

    def test_guest_key_is_stable_and_24_chars(self):
        actual = guest_key("Freda Duan", "Altimeter", "AI Podcast")
        expected_raw = "freda duan|altimeter|ai podcast"
        expected = hashlib.sha256(expected_raw.encode()).hexdigest()[:24]

        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 24)

    def test_guest_key_normalizes_case_and_outer_whitespace(self):
        self.assertEqual(
            guest_key(" Freda Duan ", " Altimeter ", " AI Podcast "),
            guest_key("freda duan", "altimeter", "ai podcast"),
        )

    def test_confirmed_ttl_uses_90_days_and_expires_only_when_greater(self):
        now = datetime(2026, 6, 8, 12, 0, tzinfo=TZ_SH)

        exactly_90_days = {
            "cached_at": (now - timedelta(days=90)).isoformat(),
            "detection_status": "confirmed_guest",
        }
        over_90_days = {
            "cached_at": (now - timedelta(days=91)).isoformat(),
            "detection_status": "confirmed_guest",
        }

        self.assertTrue(is_cache_entry_valid(exactly_90_days, now=now))
        self.assertFalse(is_cache_entry_valid(over_90_days, now=now))

    def test_non_confirmed_ttl_uses_30_days(self):
        now = datetime(2026, 6, 8, 12, 0, tzinfo=TZ_SH)

        exactly_30_days = {
            "cached_at": (now - timedelta(days=30)).isoformat(),
            "detection_status": "not_confirmed",
        }
        over_30_days = {
            "cached_at": (now - timedelta(days=31)).isoformat(),
            "detection_status": "ambiguous",
        }

        self.assertTrue(is_cache_entry_valid(exactly_30_days, now=now))
        self.assertFalse(is_cache_entry_valid(over_30_days, now=now))

    def test_invalid_or_missing_cached_at_is_invalid(self):
        now = datetime(2026, 6, 8, 12, 0, tzinfo=TZ_SH)

        self.assertFalse(is_cache_entry_valid({}, now=now))
        self.assertFalse(is_cache_entry_valid({"cached_at": "not-a-date"}, now=now))

    def test_write_cache_entry_preserves_existing_entries(self):
        now = datetime(2026, 6, 8, 12, 0, tzinfo=TZ_SH)
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "guest_profiles_cache.json")
            save_cache(cache_file, {"old-key": {"guest_name": "Old Guest"}})

            write_cache_entry(
                cache_file,
                "new-key",
                {"guest_name": "New Guest", "detection_status": "confirmed_guest"},
                now=now,
            )

            cache = load_cache(cache_file)
            self.assertEqual(cache["old-key"]["guest_name"], "Old Guest")
            self.assertEqual(cache["new-key"]["guest_name"], "New Guest")
            self.assertEqual(cache["new-key"]["cached_at"], now.isoformat())

    def test_get_cache_entry_returns_valid_and_ignores_expired_entry(self):
        now = datetime(2026, 6, 8, 12, 0, tzinfo=TZ_SH)
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "guest_profiles_cache.json")
            save_cache(
                cache_file,
                {
                    "valid": {
                        "cached_at": (now - timedelta(days=1)).isoformat(),
                        "detection_status": "confirmed_guest",
                        "guest_name": "Valid Guest",
                    },
                    "expired": {
                        "cached_at": (now - timedelta(days=91)).isoformat(),
                        "detection_status": "confirmed_guest",
                        "guest_name": "Expired Guest",
                    },
                },
            )

            self.assertEqual(get_cache_entry(cache_file, "valid", now=now)["guest_name"], "Valid Guest")
            self.assertIsNone(get_cache_entry(cache_file, "expired", now=now))
            self.assertIsNone(get_cache_entry(cache_file, "missing", now=now))


if __name__ == "__main__":
    unittest.main(verbosity=2)
