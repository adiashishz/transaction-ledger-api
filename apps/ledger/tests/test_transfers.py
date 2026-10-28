import time
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from apps.ledger import services
from apps.ledger.exceptions import InsufficientBalance
from apps.ledger.models import MAX_BALANCE, Account, Transaction, Transfer
from apps.ledger.tests.helpers import ErrorAssertions
from apps.ledger.tests.test_integrity import run_concurrently, stored_balance

TRANSFERS_URL = "/api/transfers"
UNKNOWN_ID = "00000000-0000-4000-8000-000000000999"


class TransferTestCase(ErrorAssertions, APITestCase):
    def setUp(self):
        self.source = Account.objects.create(name="Aarav Sharma")
        self.destination = Account.objects.create(name="Priya Patel")
        services.post_transaction(self.source.id, "credit", Decimal("100.00"), "OPEN-A")
        services.post_transaction(self.destination.id, "credit", Decimal("50.00"), "OPEN-B")

    def transfer(self, amount="30.00", reference_id="TRF-1", **overrides):
        payload = {
            "from_account_id": str(self.source.id),
            "to_account_id": str(self.destination.id),
            "amount": amount,
            "reference_id": reference_id,
            **overrides,
        }
        return self.client.post(TRANSFERS_URL, payload)

    def balances(self):
        self.source.refresh_from_db()
        self.destination.refresh_from_db()
        return self.source.balance, self.destination.balance

    def assert_nothing_changed(self):
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("50.00")))
        self.assertEqual(Transfer.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 2)


class TransferTests(TransferTestCase):
    def test_brief_example(self):
        """A 100, B 50, transfer 30 from A to B: A 70, B 80."""
        response = self.transfer("30.00")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.balances(), (Decimal("70.00"), Decimal("80.00")))

    def test_response_includes_both_legs(self):
        body = self.transfer("30.00", description="Rent").json()

        self.assertEqual(
            set(body),
            {"id", "from_account_id", "to_account_id", "amount", "description", "reference_id", "created_at",
             "debit_transaction", "credit_transaction"},
        )
        debit, credit = body["debit_transaction"], body["credit_transaction"]
        self.assertEqual((debit["account_id"], debit["type"], debit["amount"]), (str(self.source.id), "debit", "30.00"))
        self.assertEqual((credit["account_id"], credit["type"], credit["amount"]), (str(self.destination.id), "credit", "30.00"))
        self.assertEqual({debit["transfer_id"], credit["transfer_id"]}, {body["id"]})
        self.assertEqual({debit["reference_id"], credit["reference_id"]}, {"TRF-1"})
        self.assertEqual({debit["description"], credit["description"]}, {"Rent"})

    def test_legs_get_default_descriptions(self):
        body = self.transfer().json()
        self.assertEqual(body["debit_transaction"]["description"], "Transfer to Priya Patel")
        self.assertEqual(body["credit_transaction"]["description"], "Transfer from Aarav Sharma")

    def test_legs_appear_in_each_accounts_history(self):
        transfer_id = self.transfer().json()["id"]

        source_latest = self.client.get(f"/api/accounts/{self.source.id}/transactions").json()["data"][0]
        destination_latest = self.client.get(f"/api/accounts/{self.destination.id}/transactions").json()["data"][0]
        self.assertEqual((source_latest["type"], source_latest["transfer_id"]), ("debit", transfer_id))
        self.assertEqual((destination_latest["type"], destination_latest["transfer_id"]), ("credit", transfer_id))

    def test_ordinary_transactions_have_no_transfer_id(self):
        history = self.client.get(f"/api/accounts/{self.source.id}/transactions").json()["data"]
        self.assertIsNone(history[0]["transfer_id"])

    def test_whole_balance_can_be_transferred(self):
        self.assertEqual(self.transfer("100.00").status_code, 201)
        self.assertEqual(self.balances(), (Decimal("0.00"), Decimal("150.00")))

    def test_balances_match_stored_transactions(self):
        self.transfer("30.00", "TRF-1")
        self.client.post(
            TRANSFERS_URL,
            {"from_account_id": str(self.destination.id), "to_account_id": str(self.source.id), "amount": "45.50", "reference_id": "TRF-2"},
        )
        self.assertEqual(self.balances(), (Decimal("115.50"), Decimal("34.50")))
        self.assertEqual(self.source.balance, stored_balance(self.source))
        self.assertEqual(self.destination.balance, stored_balance(self.destination))


class TransferRejectionTests(TransferTestCase):
    def test_insufficient_balance(self):
        error = self.assert_error(self.transfer("100.01"), 422, "INSUFFICIENT_BALANCE")
        self.assertEqual(error["details"], {"balance": "100.00", "requested": "100.01", "shortfall": "0.01"})
        self.assert_nothing_changed()

    def test_destination_balance_limit(self):
        Account.objects.filter(pk=self.destination.pk).update(balance=MAX_BALANCE)
        self.assert_error(self.transfer("1.00"), 422, "BALANCE_LIMIT_EXCEEDED")
        self.assertEqual(Transfer.objects.count(), 0)
        self.source.refresh_from_db()
        self.assertEqual(self.source.balance, Decimal("100.00"))

    def test_duplicate_transfer_returns_original(self):
        original = self.transfer("30.00").json()
        error = self.assert_error(self.transfer("30.00"), 409, "DUPLICATE_TRANSFER")

        self.assertEqual(error["details"]["transfer"], original)
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(self.balances(), (Decimal("70.00"), Decimal("80.00")))

    def test_duplicate_is_reported_before_insufficient_balance(self):
        self.transfer("80.00")
        self.assert_error(self.transfer("80.00"), 409, "DUPLICATE_TRANSFER")

    def test_reference_already_used_on_source(self):
        error = self.assert_error(self.transfer(reference_id="OPEN-A"), 409, "DUPLICATE_TRANSACTION")
        self.assertIn("source account", error["message"])
        self.assert_nothing_changed()

    def test_reference_already_used_on_destination(self):
        error = self.assert_error(self.transfer(reference_id="OPEN-B"), 409, "DUPLICATE_TRANSACTION")
        self.assertIn("destination account", error["message"])
        self.assert_nothing_changed()

    def test_same_reference_from_a_different_source_is_allowed(self):
        third = Account.objects.create(name="Rohan Mehta")
        self.transfer("10.00", "SHARED")
        services.post_transaction(self.destination.id, "credit", Decimal("0.01"), "PAD")
        response = self.client.post(
            TRANSFERS_URL,
            {"from_account_id": str(self.destination.id), "to_account_id": str(third.id), "amount": "5.00", "reference_id": "SHARED-2"},
        )
        self.assertEqual(response.status_code, 201)

    def test_unknown_source(self):
        error = self.assert_error(self.transfer(from_account_id=UNKNOWN_ID), 404, "ACCOUNT_NOT_FOUND")
        self.assertEqual(error["message"], "Source account not found.")
        self.assertEqual(error["details"], {"field": "from_account_id", "account_id": UNKNOWN_ID})
        self.assert_nothing_changed()

    def test_unknown_destination(self):
        error = self.assert_error(self.transfer(to_account_id=UNKNOWN_ID), 404, "ACCOUNT_NOT_FOUND")
        self.assertEqual(error["message"], "Destination account not found.")
        self.assertEqual(error["details"], {"field": "to_account_id", "account_id": UNKNOWN_ID})
        self.assert_nothing_changed()

    def test_both_unknown_reports_source(self):
        response = self.transfer(from_account_id=UNKNOWN_ID, to_account_id="00000000-0000-4000-8000-000000000998")
        error = self.assert_error(response, 404, "ACCOUNT_NOT_FOUND")
        self.assertEqual(error["details"]["field"], "from_account_id")


class TransferValidationTests(TransferTestCase):
    def assert_field_error(self, response, field):
        error = self.assert_error(response, 400, "VALIDATION_ERROR")
        self.assertIn(field, error["details"])
        self.assert_nothing_changed()

    def test_same_account(self):
        self.assert_field_error(self.transfer(to_account_id=str(self.source.id)), "to_account_id")

    def test_required_fields(self):
        error = self.assert_error(self.client.post(TRANSFERS_URL, {}), 400, "VALIDATION_ERROR")
        self.assertEqual(set(error["details"]), {"from_account_id", "to_account_id", "amount", "reference_id"})

    def test_invalid_account_ids(self):
        for value in ["abc", "", None, 5, ["x"]]:
            with self.subTest(value=value):
                self.assert_field_error(self.transfer(from_account_id=value), "from_account_id")
                self.assert_field_error(self.transfer(to_account_id=value), "to_account_id")

    def test_invalid_amounts(self):
        for value in ["0", "-5", "1e2", "10.001", "abc", None]:
            with self.subTest(amount=value):
                self.assert_field_error(self.transfer(amount=value), "amount")

    def test_invalid_reference_id(self):
        for value in ["", "   ", 123, "x" * 101]:
            with self.subTest(reference_id=value):
                self.assert_field_error(self.transfer(reference_id=value), "reference_id")

    def test_unknown_fields(self):
        self.assert_field_error(self.transfer(type="debit"), "type")

    def test_malformed_json(self):
        response = self.client.post(TRANSFERS_URL, data='{"amount": ', content_type="application/json")
        self.assert_error(response, 400, "MALFORMED_JSON")

    def test_only_post_is_allowed(self):
        self.assert_error(self.client.get(TRANSFERS_URL), 405, "METHOD_NOT_ALLOWED")


class TransferAtomicityTests(TransferTestCase):
    def test_failure_after_debit_rolls_back_everything(self):
        """The debit is applied, then the credit fails: no part of the transfer may survive."""
        original = services._apply_balance_change

        def fail_on_credit(account, delta):
            if delta > 0:
                raise RuntimeError("credit failed")
            original(account, delta)

        with mock.patch.object(services, "_apply_balance_change", side_effect=fail_on_credit):
            with self.assertRaises(RuntimeError):
                services.create_transfer(self.source.id, self.destination.id, Decimal("30.00"), "TRF-1")
        self.assert_nothing_changed()

    def test_failure_returns_json_500_and_persists_nothing(self):
        with mock.patch.object(services, "_apply_balance_change", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.ledger.exceptions", "ERROR"):
                response = self.transfer()
        self.assert_error(response, 500, "INTERNAL_ERROR")
        self.assert_nothing_changed()

    def test_unique_constraint_backstop_on_destination_leg(self):
        """If the lookups miss a used reference, the credit leg's insert fails and the debit leg is undone."""
        with mock.patch.object(services, "_find_transaction", return_value=None):
            error = self.assert_error(self.transfer(reference_id="OPEN-B"), 409, "DUPLICATE_TRANSACTION")
        self.assertIn("destination account", error["message"])
        self.assert_nothing_changed()


class TransferConstraintTests(APITestCase):
    """The database itself refuses invalid or edited transfers, even if the API layer is bypassed."""

    def setUp(self):
        self.a = Account.objects.create(name="A")
        self.b = Account.objects.create(name="B")

    def create(self, **overrides):
        fields = {"from_account": self.a, "to_account": self.b, "amount": Decimal("1.00"), "reference_id": "R"}
        return Transfer.objects.create(**{**fields, **overrides})

    def assert_rejected(self, **overrides):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create(**overrides)

    def test_accounts_must_differ(self):
        self.assert_rejected(to_account=self.a)

    def test_amount_must_be_positive(self):
        self.assert_rejected(amount=Decimal("0.00"))

    def test_reference_is_unique_per_source(self):
        self.create()
        self.assert_rejected()

    def test_transfer_has_at_most_one_leg_of_each_type(self):
        transfer = self.create()
        Transaction.objects.create(account=self.a, type="debit", amount=Decimal("1.00"), reference_id="L1", transfer=transfer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Transaction.objects.create(account=self.b, type="debit", amount=Decimal("1.00"), reference_id="L2", transfer=transfer)

    def test_transfers_are_immutable(self):
        transfer = self.create()
        with self.assertRaisesMessage(IntegrityError, "immutable"), transaction.atomic():
            Transfer.objects.filter(pk=transfer.pk).update(amount=Decimal("999.00"))
        with self.assertRaisesMessage(IntegrityError, "immutable"), transaction.atomic():
            Transfer.objects.filter(pk=transfer.pk).delete()


def slow_locks():
    """Pause after each row lock so two opposite transfers really overlap while holding locks."""
    original = services._lock_row

    def slow_lock(account_id):
        row = original(account_id)
        time.sleep(0.2)
        return row

    return mock.patch.object(services, "_lock_row", side_effect=slow_lock)


class TransferConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.a = Account.objects.create(name="A")
        self.b = Account.objects.create(name="B")
        services.post_transaction(self.a.id, "credit", Decimal("100.00"), "OPEN-A")
        services.post_transaction(self.b.id, "credit", Decimal("100.00"), "OPEN-B")

    def move(self, source, destination, amount, reference_id):
        return lambda: services.create_transfer(source.id, destination.id, Decimal(amount), reference_id)

    def test_opposite_transfers_do_not_deadlock(self):
        """A→B and B→A at once. Locking in sorted order means one waits instead of both deadlocking."""
        with slow_locks():
            results = run_concurrently(self.move(self.a, self.b, "10.00", "AB"), self.move(self.b, self.a, "20.00", "BA"))

        self.assertTrue(all(isinstance(result, Transfer) for result in results), results)
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.balance, self.b.balance), (Decimal("110.00"), Decimal("90.00")))

    def test_money_is_conserved_under_concurrent_transfers(self):
        calls = [self.move(self.a, self.b, "15.00", f"AB-{n}") for n in range(8)]
        calls += [self.move(self.b, self.a, "25.00", f"BA-{n}") for n in range(8)]
        results = run_concurrently(*calls)

        self.assertTrue(all(isinstance(r, (Transfer, InsufficientBalance)) for r in results), results)
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.balance + self.b.balance, Decimal("200.00"))
        self.assertGreaterEqual(min(self.a.balance, self.b.balance), 0)
        self.assertEqual(self.a.balance, stored_balance(self.a))
        self.assertEqual(self.b.balance, stored_balance(self.b))

    def test_concurrent_transfers_cannot_overdraw_the_source(self):
        results = run_concurrently(*[self.move(self.a, self.b, "30.00", f"DRAIN-{n}") for n in range(5)])

        self.assertEqual(sum(isinstance(r, Transfer) for r in results), 3)
        self.assertEqual(sum(isinstance(r, InsufficientBalance) for r in results), 2)
        self.a.refresh_from_db()
        self.assertEqual(self.a.balance, Decimal("10.00"))

    def test_concurrent_duplicate_transfers_create_one(self):
        results = run_concurrently(*[self.move(self.a, self.b, "10.00", "ONCE") for _ in range(5)])

        self.assertEqual(sum(isinstance(r, Transfer) for r in results), 1)
        self.assertEqual(Transfer.objects.count(), 1)
        self.a.refresh_from_db()
        self.assertEqual(self.a.balance, Decimal("90.00"))
