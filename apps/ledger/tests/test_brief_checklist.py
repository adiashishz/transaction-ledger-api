"""The assessment brief's checklists, one test per bullet, so each rule is easy to find.

Each test asserts the HTTP status and the error code. Deeper coverage lives in the
per-feature test modules.
"""

from decimal import Decimal

from rest_framework.test import APITestCase

from apps.ledger import services
from apps.ledger.models import Account, Transaction
from apps.ledger.tests.helpers import ErrorAssertions

UNKNOWN_ID = "00000000-0000-4000-8000-000000000999"


class BriefChecklistTestCase(ErrorAssertions, APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")
        services.post_transaction(self.account.id, "credit", Decimal("50.00"), "REF-1")
        self.url = f"/api/accounts/{self.account.id}/transactions"

    def post_transaction(self, url=None, **overrides):
        payload = {"type": "credit", "amount": "10.00", "reference_id": "REF-NEW", **overrides}
        return self.client.post(url or self.url, {k: v for k, v in payload.items() if v is not None})

    def assert_nothing_changed(self):
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("50.00"))
        self.assertEqual(Transaction.objects.count(), 1)


class BriefValidationTests(BriefChecklistTestCase):
    """Brief section 7: Validations."""

    def test_account_name_must_be_provided(self):
        error = self.assert_error(self.client.post("/api/accounts", {}), 400, "VALIDATION_ERROR")
        self.assertEqual(error["details"], {"name": ["This field is required."]})

    def test_account_id_must_exist_when_creating_a_transaction(self):
        self.assert_error(self.post_transaction(url=f"/api/accounts/{UNKNOWN_ID}/transactions"), 404, "ACCOUNT_NOT_FOUND")
        self.assert_nothing_changed()

    def test_transaction_type_must_be_credit_or_debit(self):
        error = self.assert_error(self.post_transaction(type="refund"), 400, "VALIDATION_ERROR")
        self.assertEqual(error["details"], {"type": ['"refund" is not a valid choice.']})
        self.assert_nothing_changed()

    def test_amount_must_be_greater_than_zero(self):
        for amount in ["0", "0.00", "-10.00"]:
            with self.subTest(amount=amount):
                error = self.assert_error(self.post_transaction(amount=amount), 400, "VALIDATION_ERROR")
                self.assertEqual(error["details"], {"amount": ["Amount must be greater than 0."]})
        self.assert_nothing_changed()

    def test_reference_id_must_be_provided(self):
        error = self.assert_error(self.post_transaction(reference_id=None), 400, "VALIDATION_ERROR")
        self.assertEqual(error["details"], {"reference_id": ["This field is required."]})
        self.assert_nothing_changed()

    def test_duplicate_reference_id_for_the_same_account_is_rejected(self):
        for reference_id in ["REF-1", "  REF-1  "]:
            with self.subTest(reference_id=reference_id):
                self.assert_error(self.post_transaction(reference_id=reference_id), 409, "DUPLICATE_TRANSACTION")
        self.assert_nothing_changed()

    def test_debit_must_have_sufficient_balance(self):
        self.assert_error(self.post_transaction(type="debit", amount="50.01"), 422, "INSUFFICIENT_BALANCE")
        self.assert_nothing_changed()

    def test_invalid_account_ids_return_an_error(self):
        for path in ["", "/balance", "/transactions"]:
            with self.subTest(path=path):
                url = f"/api/accounts/not-a-uuid{path}"
                self.assert_error(self.client.get(url), 400, "INVALID_ACCOUNT_ID")
        self.assert_error(self.post_transaction(url="/api/accounts/not-a-uuid/transactions"), 400, "INVALID_ACCOUNT_ID")

    def test_invalid_request_data_returns_a_validation_error(self):
        cases = [
            ("body is a list", ["credit", "10.00"]),
            ("wrong types", {"type": 1, "amount": True, "reference_id": 5}),
            ("unknown field", {"type": "credit", "amount": "10.00", "reference_id": "R", "balance": 1}),
        ]
        for label, payload in cases:
            with self.subTest(label):
                self.assert_error(self.client.post(self.url, payload), 400, "VALIDATION_ERROR")
        malformed = self.client.post(self.url, data='{"type": ', content_type="application/json")
        self.assert_error(malformed, 400, "MALFORMED_JSON")
        not_json = self.client.post(self.url, data="type=credit", content_type="application/x-www-form-urlencoded")
        self.assert_error(not_json, 415, "UNSUPPORTED_MEDIA_TYPE")
        self.assert_nothing_changed()


class BriefErrorHandlingTests(BriefChecklistTestCase):
    """Brief section 8: Error handling, with proper status codes and consistent, understandable bodies."""

    def assert_understandable(self, response, status_code, code):
        error = self.assert_error(response, status_code, code)
        self.assertTrue(error["message"].strip(), "message must not be empty")
        self.assertIsInstance(error["details"], dict)
        return error

    def test_account_not_found(self):
        for path in ["", "/balance", "/transactions"]:
            with self.subTest(path=path):
                response = self.client.get(f"/api/accounts/{UNKNOWN_ID}{path}")
                error = self.assert_understandable(response, 404, "ACCOUNT_NOT_FOUND")
                self.assertEqual(error["details"], {"account_id": UNKNOWN_ID})

    def test_invalid_transaction_type(self):
        error = self.assert_understandable(self.post_transaction(type="refund"), 400, "VALIDATION_ERROR")
        self.assertEqual(error["details"], {"type": ['"refund" is not a valid choice.']})

    def test_invalid_amount(self):
        cases = {
            "abc": "Amount must be a plain decimal like 100 or 100.50.",
            "1e2": "Amount must be a plain decimal like 100 or 100.50.",
            "-1": "Amount must be greater than 0.",
            "1.001": "Ensure that there are no more than 2 decimal places.",
        }
        for amount, message in cases.items():
            with self.subTest(amount=amount):
                error = self.assert_understandable(self.post_transaction(amount=amount), 400, "VALIDATION_ERROR")
                self.assertEqual(error["details"], {"amount": [message]})

    def test_insufficient_balance(self):
        response = self.post_transaction(type="debit", amount="80.00")
        error = self.assert_understandable(response, 422, "INSUFFICIENT_BALANCE")
        self.assertEqual(error["message"], "Insufficient funds.")
        self.assertEqual(error["details"], {"balance": "50.00", "requested": "80.00", "shortfall": "30.00"})

    def test_duplicate_transaction(self):
        error = self.assert_understandable(self.post_transaction(reference_id="REF-1"), 409, "DUPLICATE_TRANSACTION")
        self.assertIn("REF-1", error["message"])
        self.assertEqual(error["details"]["transaction"]["reference_id"], "REF-1")

    def test_invalid_pagination_parameters(self):
        for url in [self.url, "/api/accounts"]:
            for params, field in [({"page": 0}, "page"), ({"limit": 1000}, "limit"), ({"page": "x"}, "page")]:
                with self.subTest(url=url, params=params):
                    error = self.assert_understandable(self.client.get(url, params), 400, "VALIDATION_ERROR")
                    self.assertIn(field, error["details"])

    def test_invalid_request_body(self):
        empty = self.assert_understandable(self.client.post(self.url, {}), 400, "VALIDATION_ERROR")
        self.assertEqual(set(empty["details"]), {"type", "amount", "reference_id"})
        malformed = self.client.post(self.url, data="{not json", content_type="application/json")
        self.assert_understandable(malformed, 400, "MALFORMED_JSON")
        self.assert_nothing_changed()
