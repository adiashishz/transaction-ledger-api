import json
from datetime import datetime, timedelta, timezone
from unittest import mock

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at_minute(minute):
    """Pin `created_at` (auto_now_add reads timezone.now) so ordering tests don't depend on timing."""
    return mock.patch("django.utils.timezone.now", return_value=BASE_TIME + timedelta(minutes=minute))


class ErrorAssertions:
    """Mixin for TestCases: asserts the shared `{error: {code, message, details}}` body."""

    def assert_error(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code, response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        body = json.loads(response.content)
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        self.assertEqual(body["error"]["code"], code)
        return body["error"]
