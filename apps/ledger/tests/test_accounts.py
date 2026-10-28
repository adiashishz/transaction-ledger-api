from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework.test import APITestCase

from apps.ledger import services
from apps.ledger.models import Account
from apps.ledger.tests.helpers import ErrorAssertions, at_minute

ACCOUNTS_URL = "/api/accounts"


class CreateAccountTests(ErrorAssertions, APITestCase):
    def test_creates_account_with_zero_balance(self):
        response = self.client.post(ACCOUNTS_URL, {"name": "Aarav Sharma"})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(set(body), {"id", "name", "balance", "created_at"})
        self.assertEqual(body["name"], "Aarav Sharma")
        self.assertEqual(body["balance"], "0.00")
        account = Account.objects.get(pk=body["id"])
        self.assertEqual(account.balance, Decimal("0.00"))

    def test_each_account_gets_a_unique_id(self):
        first = self.client.post(ACCOUNTS_URL, {"name": "Same Name"}).json()
        second = self.client.post(ACCOUNTS_URL, {"name": "Same Name"}).json()
        self.assertNotEqual(first["id"], second["id"])

    def test_name_is_trimmed(self):
        response = self.client.post(ACCOUNTS_URL, {"name": "  Priya Patel  "})
        self.assertEqual(response.json()["name"], "Priya Patel")

    def test_name_is_required(self):
        error = self.assert_error(self.client.post(ACCOUNTS_URL, {}), 400, "VALIDATION_ERROR")
        self.assertIn("name", error["details"])

    def test_invalid_names_are_rejected(self):
        for name in ["", "   ", None, 123, True, ["Aarav"], "x" * 256]:
            with self.subTest(name=name):
                response = self.client.post(ACCOUNTS_URL, {"name": name})
                error = self.assert_error(response, 400, "VALIDATION_ERROR")
                self.assertIn("name", error["details"])
        self.assertEqual(Account.objects.count(), 0)

    def test_client_cannot_set_balance(self):
        response = self.client.post(ACCOUNTS_URL, {"name": "Rohan Mehta", "balance": 5000})
        error = self.assert_error(response, 400, "VALIDATION_ERROR")
        self.assertIn("balance", error["details"])
        self.assertEqual(Account.objects.count(), 0)

    def test_client_cannot_set_server_fields(self):
        response = self.client.post(
            ACCOUNTS_URL, {"name": "Rohan", "id": "abc", "created_at": "2020-01-01", "extra": 1}
        )
        error = self.assert_error(response, 400, "VALIDATION_ERROR")
        self.assertEqual(set(error["details"]), {"id", "created_at", "extra"})

    def test_unknown_field_and_field_errors_are_reported_together(self):
        error = self.assert_error(self.client.post(ACCOUNTS_URL, {"balance": 1}), 400, "VALIDATION_ERROR")
        self.assertEqual(set(error["details"]), {"name", "balance"})

    def test_body_must_be_an_object(self):
        error = self.assert_error(self.client.post(ACCOUNTS_URL, ["Aarav"]), 400, "VALIDATION_ERROR")
        self.assertIn("non_field_errors", error["details"])

    def test_malformed_json_is_rejected(self):
        response = self.client.post(ACCOUNTS_URL, data='{"name": ', content_type="application/json")
        self.assert_error(response, 400, "MALFORMED_JSON")

    def test_non_json_content_type_is_rejected(self):
        response = self.client.post(ACCOUNTS_URL, data="name=Aarav", content_type="text/plain")
        self.assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")

    def test_other_methods_are_not_allowed(self):
        for response in [self.client.put(ACCOUNTS_URL, {}), self.client.patch(ACCOUNTS_URL, {}), self.client.delete(ACCOUNTS_URL)]:
            self.assert_error(response, 405, "METHOD_NOT_ALLOWED")


class ListAccountsTests(ErrorAssertions, APITestCase):
    def create_at(self, minute, name):
        with at_minute(minute):
            return Account.objects.create(name=name)

    def test_empty_list(self):
        response = self.client.get(ACCOUNTS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"data": [], "pagination": {"page": 1, "limit": 10, "total_records": 0, "total_pages": 0}},
        )

    def test_lists_accounts_newest_first(self):
        for minute, name in enumerate(["Aarav", "Priya", "Rohan"], start=1):
            self.create_at(minute, name)
        body = self.client.get(ACCOUNTS_URL).json()

        self.assertEqual([account["name"] for account in body["data"]], ["Rohan", "Priya", "Aarav"])
        self.assertEqual(set(body["data"][0]), {"id", "name", "balance", "created_at"})
        self.assertEqual(body["pagination"]["total_records"], 3)

    def test_paginates(self):
        for minute in range(1, 6):
            self.create_at(minute, f"Account {minute}")
        body = self.client.get(ACCOUNTS_URL, {"page": 2, "limit": 2}).json()

        self.assertEqual([account["name"] for account in body["data"]], ["Account 3", "Account 2"])
        self.assertEqual(body["pagination"], {"page": 2, "limit": 2, "total_records": 5, "total_pages": 3})

    def test_invalid_pagination_params(self):
        for params, field in [({"page": 0}, "page"), ({"limit": 101}, "limit"), ({"limit": "x"}, "limit")]:
            with self.subTest(params=params):
                error = self.assert_error(self.client.get(ACCOUNTS_URL, params), 400, "VALIDATION_ERROR")
                self.assertIn(field, error["details"])


class GetAccountTests(ErrorAssertions, APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")

    def test_returns_account_details(self):
        response = self.client.get(f"{ACCOUNTS_URL}/{self.account.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": str(self.account.id),
                "name": "Aarav Sharma",
                "balance": "0.00",
                "created_at": response.json()["created_at"],
            },
        )

    def test_malformed_id_is_rejected(self):
        for bad_id in ["abc", "123", "00000000-0000-4000-8000-00000000000z"]:
            with self.subTest(bad_id=bad_id):
                error = self.assert_error(self.client.get(f"{ACCOUNTS_URL}/{bad_id}"), 400, "INVALID_ACCOUNT_ID")
                self.assertEqual(error["details"], {"account_id": bad_id})

    def test_unknown_id_is_not_found(self):
        unknown_id = "00000000-0000-4000-8000-000000000999"
        error = self.assert_error(self.client.get(f"{ACCOUNTS_URL}/{unknown_id}"), 404, "ACCOUNT_NOT_FOUND")
        self.assertEqual(error["details"], {"account_id": unknown_id})

    def test_account_cannot_be_changed_or_deleted(self):
        url = f"{ACCOUNTS_URL}/{self.account.id}"
        for response in [
            self.client.put(url, {"name": "New"}),
            self.client.patch(url, {"balance": 100}),
            self.client.delete(url),
        ]:
            self.assert_error(response, 405, "METHOD_NOT_ALLOWED")
        self.account.refresh_from_db()
        self.assertEqual((self.account.name, self.account.balance), ("Aarav Sharma", Decimal("0.00")))


class GetBalanceTests(ErrorAssertions, APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")
        self.url = f"{ACCOUNTS_URL}/{self.account.id}/balance"

    def test_new_account_has_zero_balance(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"account_id": str(self.account.id), "balance": "0.00"})

    def test_balance_reflects_transactions(self):
        services.post_transaction(self.account.id, "credit", Decimal("250.00"), "REF-1")
        services.post_transaction(self.account.id, "debit", Decimal("75.50"), "REF-2")
        self.assertEqual(self.client.get(self.url).json()["balance"], "174.50")

    def test_balance_is_unchanged_by_rejected_debit(self):
        services.post_transaction(self.account.id, "credit", Decimal("100.00"), "REF-1")
        self.client.post(
            f"{ACCOUNTS_URL}/{self.account.id}/transactions",
            {"type": "debit", "amount": "150.00", "reference_id": "REF-2"},
        )
        self.assertEqual(self.client.get(self.url).json()["balance"], "100.00")

    def test_malformed_id_is_rejected(self):
        self.assert_error(self.client.get(f"{ACCOUNTS_URL}/abc/balance"), 400, "INVALID_ACCOUNT_ID")

    def test_unknown_id_is_not_found(self):
        response = self.client.get(f"{ACCOUNTS_URL}/00000000-0000-4000-8000-000000000999/balance")
        self.assert_error(response, 404, "ACCOUNT_NOT_FOUND")

    def test_balance_cannot_be_written(self):
        for response in [self.client.post(self.url, {"balance": 100}), self.client.put(self.url, {"balance": 100})]:
            self.assert_error(response, 405, "METHOD_NOT_ALLOWED")
        self.assertEqual(self.client.get(self.url).json()["balance"], "0.00")


class AccountConstraintTests(APITestCase):
    """The database itself refuses invalid accounts, even if the API layer is bypassed."""

    def test_negative_balance_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(name="Overdrawn", balance=Decimal("-0.01"))

    def test_blank_name_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Account.objects.create(name="   ")
