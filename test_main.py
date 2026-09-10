import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("X_USERNAME", "target_user")
os.environ.setdefault("TO_EMAIL", "test@example.com")
os.environ.setdefault("RESEND_API_KEY", "test-key")

fake_resend = types.ModuleType("resend")
fake_resend.Emails = types.SimpleNamespace(send=lambda message: None)
sys.modules.setdefault("resend", fake_resend)

notifier = importlib.import_module("main")


def yahoo_document(entries):
    data = {"props": {"pageProps": {"pageData": {"timeline": {"entry": entries}}}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data)
        + "</script>"
    )


class NotifierTests(unittest.TestCase):
    def test_extracts_only_target_account_and_canonicalizes_url(self):
        document = yahoo_document([
            {"id": "101", "screenName": "target_user", "displayTextBody": "hello", "url": "https://x.com/target_user/status/101?utm_source=yjrealtime"},
            {"id": "999", "screenName": "other", "displayTextBody": "ignore", "url": "https://x.com/other/status/999"},
        ])
        self.assertEqual(notifier.extract_yahoo_posts(document), [{
            "id": "101", "url": "https://x.com/target_user/status/101", "text": "hello"
        }])

    def test_missing_yahoo_data_fails(self):
        with self.assertRaises(RuntimeError):
            notifier.extract_yahoo_posts("<html></html>")

    def test_new_posts_are_sorted_and_state_advances_after_mail(self):
        posts = [
            {"id": "103", "url": "https://x.com/target_user/status/103", "text": "third"},
            {"id": "101", "url": "https://x.com/target_user/status/101", "text": "first"},
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(notifier, "LAST_FILE", Path(directory) / "last.txt"), patch.object(notifier, "get_posts", return_value=posts), patch.object(notifier, "send_mail") as send_mail:
            notifier.LAST_FILE.write_text("100")
            notifier.main()
            self.assertEqual([p["id"] for p in send_mail.call_args.args[0]], ["101", "103"])
            self.assertEqual(notifier.LAST_FILE.read_text(), "103")

    def test_mail_failure_does_not_advance_state(self):
        posts = [{"id": "101", "url": "https://x.com/target_user/status/101", "text": "new"}]
        with tempfile.TemporaryDirectory() as directory, patch.object(notifier, "LAST_FILE", Path(directory) / "last.txt"), patch.object(notifier, "get_posts", return_value=posts), patch.object(notifier, "send_mail", side_effect=RuntimeError("send failed")):
            notifier.LAST_FILE.write_text("100")
            with self.assertRaises(RuntimeError):
                notifier.main()
            self.assertEqual(notifier.LAST_FILE.read_text(), "100")


if __name__ == "__main__":
    unittest.main()
