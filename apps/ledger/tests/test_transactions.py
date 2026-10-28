from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from rest_framework.test import APITestCase

from apps.ledger import services
from apps.ledger.models import Account, Transaction
from apps.ledger.tests.helpers import ErrorAssertions


def transactions_url(account_id):
    return f"/api/accounts/{account_id}/transactions"


class TransactionTestCase(ErrorAssertions, APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")
        self.url = transactions_url(self.account.id)

    def post(self, type="credit", amount="100.00", reference_id="REF-1", **extra):
        payload = {"type": type, "amount": amount, "reference_id": reference_id, **extra}
        return self.client.post(self.url, payload)

    def balance(self):
        self.account.refresh_from_db()
        return self.account.balance


class AddTransactionTests(TransactionTestCase):
    def test_credit_increases_balance(self):
        self.post("credit", "100.00", "REF-1")
        response = self.post("credit", "50.00", "REF-2", description="Top-up")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(
            set(body), {"id", "account_id", "type", "amount", "description", "reference_id", "created_at"}
        )
        self.assertEqual(body["account_id"], str(self.account.id))
        self.assertEqual((body["type"], body["amount"], body["description"]), ("credit", "50.00", "Top-up"))
        self.assertEqual(self.balance(), Decimal("150.00"))

    def test_debit_decreases_balance(self):
        self.post("credit", "100.00", "REF-1")
        response = self.post("debit", "30.00", "REF-2")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.balance(), Decimal("70.00"))

    def test_debit_can_empty_the_account(self):
        self.post("credit", "100.00", "REF-1")
        self.assertEqual(self.post("debit", "100.00", "REF-2").status_code, 201)
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_brief_example_sequence(self):
        for ref, (type_, amount) in enumerate([("credit", 100), ("credit", 50), ("debit", 30), ("debit", 20)]):
            self.assertEqual(self.post(type_, amount, f"REF-{ref}").status_code, 201)
        self.assertEqual(self.balance(), Decimal("100.00"))

    def test_amount_accepts_numbers_and_numeric_strings(self):
        self.assertEqual(self.post(amount=10, reference_id="A").json()["amount"], "10.00")
        self.assertEqual(self.post(amount=0.5, reference_id="B").json()["amount"], "0.50")
        self.assertEqual(self.post(amount="25.1", reference_id="C").json()["amount"], "25.10")
        self.assertEqual(self.balance(), Decimal("35.60"))

    def test_description_is_optional(self):
        self.assertEqual(self.post().json()["description"], "")


class InsufficientBalanceTests(TransactionTestCase):
    def test_debit_more_than_balance_is_rejected(self):
        self.post("credit", "100.00", "REF-1")
        error = self.assert_error(self.post("debit", "150.00", "REF-2"), 422, "INSUFFICIENT_BALANCE")

        self.assertEqual(error["details"], {"balance": "100.00", "requested": "150.00", "shortfall": "50.00"})
        self.assertEqual(error["message"], "Insufficient funds.")
        self.assertEqual(self.balance(), Decimal("100.00"))
        self.assertFalse(Transaction.objects.filter(reference_id="REF-2").exists())

    def test_debit_on_empty_account_is_rejected(self):
        self.assert_error(self.post("debit", "0.01"), 422, "INSUFFICIENT_BALANCE")
        self.assertEqual(Transaction.objects.count(), 0)

    def test_rejected_reference_id_can_be_reused(self):
        self.assert_error(self.post("debit", "50.00", "REF-1"), 422, "INSUFFICIENT_BALANCE")
        self.assertEqual(self.post("credit", "50.00", "REF-1").status_code, 201)


class DuplicateTransactionTests(TransactionTestCase):
    def test_same_reference_id_is_rejected_and_returns_original(self):
        original = self.post("credit", "100.00", "REF-1").json()
        error = self.assert_error(self.post("credit", "100.00", "REF-1"), 409, "DUPLICATE_TRANSACTION")

        self.assertEqual(error["details"]["transaction"], original)
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(self.balance(), Decimal("100.00"))

    def test_duplicate_with_different_payload_is_still_rejected(self):
        original = self.post("credit", "100.00", "REF-1").json()
        error = self.assert_error(self.post("debit", "5.00", "REF-1"), 409, "DUPLICATE_TRANSACTION")

        self.assertEqual(error["details"]["transaction"], original)
        self.assertEqual(self.balance(), Decimal("100.00"))

    def test_duplicate_is_reported_before_insufficient_balance(self):
        """A retried debit that already succeeded must say 'duplicate', not 'insufficient'."""
        self.post("credit", "100.00", "REF-1")
        self.post("debit", "80.00", "REF-2")
        self.assert_error(self.post("debit", "80.00", "REF-2"), 409, "DUPLICATE_TRANSACTION")
        self.assertEqual(self.balance(), Decimal("20.00"))

    def test_same_reference_id_is_allowed_on_different_accounts(self):
        other = Account.objects.create(name="Priya Patel")
        self.assertEqual(self.post(reference_id="SHARED").status_code, 201)
        response = self.client.post(
            transactions_url(other.id), {"type": "credit", "amount": "10.00", "reference_id": "SHARED"}
        )
        self.assertEqual(response.status_code, 201)

    def test_unique_constraint_backstop_returns_409(self):
        """If the lookup misses a duplicate, the database constraint still stops it."""
        original = self.post("credit", "100.00", "REF-1").json()
        with mock.patch.object(services, "_find_transaction", return_value=None):
            error = self.assert_error(self.post("credit", "100.00", "REF-1"), 409, "DUPLICATE_TRANSACTION")

        self.assertEqual(error["details"]["transaction"], original)
        self.assertEqual(self.balance(), Decimal("100.00"))


class TransactionValidationTests(TransactionTestCase):
    def assert_field_error(self, payload, field):
        error = self.assert_error(self.client.post(self.url, payload), 400, "VALIDATION_ERROR")
        self.assertIn(field, error["details"])

    def valid_payload(self, **overrides):
        return {"type": "credit", "amount": "10.00", "reference_id": "REF-1", **overrides}

    def test_invalid_type(self):
        for value in ["refund", "CREDIT", "", None, 1, True]:
            with self.subTest(type=value):
                self.assert_field_error(self.valid_payload(type=value), "type")

    def test_invalid_amount(self):
        for value in [0, "0.00", -5, "-0.01", "abc", "", None, True, "NaN", "Infinity", "10.001", 10_000_000_000]:
            with self.subTest(amount=value):
                self.assert_field_error(self.valid_payload(amount=value), "amount")

    def test_invalid_reference_id(self):
        for value in ["", "   ", None, 123, "x" * 101]:
            with self.subTest(reference_id=value):
                self.assert_field_error(self.valid_payload(reference_id=value), "reference_id")

    def test_invalid_description(self):
        for value in [None, 123, "x" * 256]:
            with self.subTest(description=value):
                self.assert_field_error(self.valid_payload(description=value), "description")

    def test_required_fields(self):
        error = self.assert_error(self.client.post(self.url, {}), 400, "VALIDATION_ERROR")
        self.assertEqual(set(error["details"]), {"type", "amount", "reference_id"})

    def test_unknown_fields_are_rejected(self):
        for field in ["account_id", "balance", "id", "created_at"]:
            with self.subTest(field=field):
                self.assert_field_error(self.valid_payload(**{field: "x"}), field)

    def test_nothing_is_written_on_validation_error(self):
        self.client.post(self.url, self.valid_payload(amount=-1))
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_malformed_json(self):
        response = self.client.post(self.url, data='{"type": ', content_type="application/json")
        self.assert_error(response, 400, "MALFORMED_JSON")


class TransactionAccountTests(TransactionTestCase):
    def test_unknown_account_is_not_found(self):
        url = transactions_url("00000000-0000-4000-8000-000000000999")
        response = self.client.post(url, {"type": "credit", "amount": "10.00", "reference_id": "REF-1"})
        self.assert_error(response, 404, "ACCOUNT_NOT_FOUND")

    def test_malformed_account_id_is_rejected(self):
        response = self.client.post(transactions_url("abc"), {"type": "credit", "amount": "10.00", "reference_id": "R"})
        self.assert_error(response, 400, "INVALID_ACCOUNT_ID")

    def test_malformed_account_id_is_checked_before_body(self):
        self.assert_error(self.client.post(transactions_url("abc"), {}), 400, "INVALID_ACCOUNT_ID")

    def test_body_is_validated_before_account_lookup(self):
        url = transactions_url("00000000-0000-4000-8000-000000000999")
        self.assert_error(self.client.post(url, {}), 400, "VALIDATION_ERROR")


class TransactionConstraintTests(APITestCase):
    """The database itself refuses invalid transactions, even if the API layer is bypassed."""

    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")

    def create(self, **overrides):
        fields = {"account": self.account, "type": "credit", "amount": Decimal("1.00"), "reference_id": "REF-1"}
        with self.assertRaises(IntegrityError), transaction.atomic():
            Transaction.objects.create(**{**fields, **overrides})

    def test_amount_must_be_positive(self):
        self.create(amount=Decimal("0.00"))
        self.create(amount=Decimal("-1.00"))

    def test_type_must_be_credit_or_debit(self):
        self.create(type="refund")

    def test_reference_id_must_not_be_blank(self):
        self.create(reference_id="  ")

    def test_reference_id_is_unique_per_account(self):
        Transaction.objects.create(account=self.account, type="credit", amount=Decimal("1.00"), reference_id="REF-1")
        self.create()
