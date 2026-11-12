import hashlib
import hmac
import os
import unittest

os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
os.environ["GITHUB_REPOSITORIES"] = "owner/frontend,owner/api"
os.environ["GITHUB_WORKFLOW_NAMES"] = "Build Image"

import github_webhook as webhook  # noqa: E402


class WebhookTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "action": "completed",
            "repository": {"full_name": "owner/frontend"},
            "workflow_run": {
                "conclusion": "success",
                "head_branch": "main",
                "name": "Build Image",
                "head_sha": "abc123",
            },
        }

    def test_accepts_valid_signature(self) -> None:
        body = b'{"test": true}'
        signature = "sha256=" + hmac.new(
            b"test-secret", body, hashlib.sha256
        ).hexdigest()
        self.assertTrue(webhook.valid_signature(body, signature))

    def test_rejects_invalid_signature(self) -> None:
        self.assertFalse(webhook.valid_signature(b"payload", "sha256=invalid"))

    def test_accepts_successful_main_workflow(self) -> None:
        self.assertEqual(
            webhook.deploy_candidate("workflow_run", self.payload()),
            (True, "abc123"),
        )

    def test_rejects_failed_workflow(self) -> None:
        payload = self.payload()
        payload["workflow_run"]["conclusion"] = "failure"
        self.assertFalse(webhook.deploy_candidate("workflow_run", payload)[0])

    def test_rejects_unlisted_repository(self) -> None:
        payload = self.payload()
        payload["repository"]["full_name"] = "other/repo"
        self.assertFalse(webhook.deploy_candidate("workflow_run", payload)[0])


if __name__ == "__main__":
    unittest.main()
