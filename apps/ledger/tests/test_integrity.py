"""Data integrity: atomicity, balance consistency, immutability and concurrency.

Concurrency tests use TransactionTestCase so each thread commits on its own database
connection, the way separate requests do in production.
"""

import random
import threading
import time
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.db.models import Sum
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from apps.ledger import services
from apps.ledger.exceptions import DuplicateTransaction, InsufficientBalance
from apps.ledger.models import Account, Transaction
from apps.ledger.tests.helpers import ErrorAssertions


def stored_balance(account):
    """What the balance must be: the sum of every stored transaction for the account."""
    totals = dict(account.transactions.values_list("type").annotate(total=Sum("amount")))
    return totals.get("credit", Decimal("0")) - totals.get("debit", Decimal("0"))


def run_concurrently(*calls):
    """Start every call at the same moment on its own thread; return each result or raised exception."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def worker(index, call):
        try:
            barrier.wait()
            results[index] = call()
        except Exception as exc:
            results[index] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(index, call)) for index, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def slow_lookups():
    """Widen the race window: pause inside the locked section, after the duplicate lookup."""
    original = services._find_transaction

    def slow_find(account, reference_id):
        found = original(account, reference_id)
        time.sleep(0.2)
        return found

    return mock.patch.object(services, "_find_transaction", side_effect=slow_find)


class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")
        services.post_transaction(self.account.id, "credit", Decimal("100.00"), "OPENING")

    def post(self, transaction_type, amount, reference_id):
        return lambda: services.post_transaction(self.account.id, transaction_type, Decimal(amount), reference_id)

    def test_concurrent_debits_cannot_overdraw(self):
        """Balance 100, two debits of 80 at once: exactly one succeeds."""
        with slow_lookups():
            results = run_concurrently(self.post("debit", "80.00", "D-1"), self.post("debit", "80.00", "D-2"))

        self.assertEqual(sum(isinstance(result, Transaction) for result in results), 1)
        self.assertEqual(sum(isinstance(result, InsufficientBalance) for result in results), 1)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("20.00"))

    def test_concurrent_requests_with_same_reference_create_one_transaction(self):
        with slow_lookups():
            results = run_concurrently(*[self.post("credit", "10.00", "SAME-REF") for _ in range(5)])

        self.assertEqual(sum(isinstance(result, Transaction) for result in results), 1)
        self.assertEqual(sum(isinstance(result, DuplicateTransaction) for result in results), 4)
        self.assertEqual(self.account.transactions.filter(reference_id="SAME-REF").count(), 1)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("110.00"))

    def test_balance_matches_transactions_after_concurrent_mixed_load(self):
        calls = [
            self.post("credit" if n % 3 else "debit", "15.00" if n % 3 else "40.00", f"MIX-{n}")
            for n in range(12)
        ]
        results = run_concurrently(*calls)

        self.account.refresh_from_db()
        self.assertTrue(all(isinstance(r, (Transaction, InsufficientBalance)) for r in results), results)
        self.assertGreaterEqual(self.account.balance, 0)
        self.assertEqual(self.account.balance, stored_balance(self.account))


class AtomicityTests(ErrorAssertions, APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")
        services.post_transaction(self.account.id, "credit", Decimal("100.00"), "OPENING")

    def test_failed_balance_update_rolls_back_the_transaction(self):
        with mock.patch.object(services, "_apply_balance_change", side_effect=RuntimeError("db went away")):
            with self.assertRaises(RuntimeError):
                services.post_transaction(self.account.id, "credit", Decimal("50.00"), "REF-1")

        self.assertFalse(Transaction.objects.filter(reference_id="REF-1").exists())
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("100.00"))

    def test_failure_returns_json_500_and_persists_nothing(self):
        url = f"/api/accounts/{self.account.id}/transactions"
        with mock.patch.object(services, "_apply_balance_change", side_effect=RuntimeError("db went away")):
            with self.assertLogs("apps.ledger.exceptions", "ERROR"):
                response = self.client.post(url, {"type": "debit", "amount": "30.00", "reference_id": "REF-1"})

        error = self.assert_error(response, 500, "INTERNAL_ERROR")
        self.assertNotIn("db went away", error["message"])
        self.assertEqual(self.account.transactions.count(), 1)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("100.00"))

    def test_reference_id_is_free_again_after_a_rolled_back_attempt(self):
        with mock.patch.object(services, "_apply_balance_change", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                services.post_transaction(self.account.id, "credit", Decimal("50.00"), "REF-1")

        services.post_transaction(self.account.id, "credit", Decimal("50.00"), "REF-1")
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("150.00"))


class BalanceConsistencyTests(APITestCase):
    def test_brief_example(self):
        account = Account.objects.create(name="Aarav Sharma")
        for ref, (transaction_type, amount) in enumerate(
            [("credit", "100"), ("credit", "50"), ("debit", "30"), ("debit", "20")]
        ):
            services.post_transaction(account.id, transaction_type, Decimal(amount), f"REF-{ref}")

        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal("100.00"))
        self.assertEqual(stored_balance(account), Decimal("100.00"))

    def test_balance_matches_transactions_after_random_sequence(self):
        """Rejected debits and duplicates are mixed in; the balance must still equal Σcredits − Σdebits."""
        account = Account.objects.create(name="Priya Patel")
        rng = random.Random(42)
        for n in range(200):
            transaction_type = rng.choice(["credit", "debit"])
            amount = Decimal(rng.randint(1, 50_000)) / 100
            reference_id = f"REF-{rng.randint(0, 150)}"
            try:
                services.post_transaction(account.id, transaction_type, amount, reference_id)
            except (InsufficientBalance, DuplicateTransaction):
                pass
            account.refresh_from_db()
            self.assertGreaterEqual(account.balance, 0)

        self.assertEqual(account.balance, stored_balance(account))


class ImmutabilityTests(ErrorAssertions, APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")
        self.txn = services.post_transaction(self.account.id, "credit", Decimal("100.00"), "REF-1", "Opening")
        self.url = f"/api/accounts/{self.account.id}/transactions"

    def assert_unchanged(self):
        txn = Transaction.objects.get(pk=self.txn.pk)
        self.assertEqual(
            (txn.type, txn.amount, txn.description, txn.reference_id, txn.account_id, txn.created_at),
            ("credit", Decimal("100.00"), "Opening", "REF-1", self.account.id, self.txn.created_at),
        )

    def test_api_has_no_way_to_edit_or_delete(self):
        for response in [
            self.client.put(self.url, {"amount": "1.00"}),
            self.client.patch(self.url, {"amount": "1.00"}),
            self.client.delete(self.url),
        ]:
            self.assert_error(response, 405, "METHOD_NOT_ALLOWED")
        self.assert_error(self.client.delete(f"{self.url}/{self.txn.id}"), 404, "NOT_FOUND")
        self.assert_unchanged()

    def test_database_rejects_updates(self):
        attempts = [
            lambda: Transaction.objects.filter(pk=self.txn.pk).update(amount=Decimal("1.00")),
            lambda: Transaction.objects.filter(pk=self.txn.pk).update(description="edited"),
        ]
        for attempt in attempts:
            with self.assertRaisesMessage(IntegrityError, "immutable"), transaction.atomic():
                attempt()
        self.assert_unchanged()

    def test_database_rejects_save_of_modified_instance(self):
        self.txn.amount = Decimal("999.00")
        with self.assertRaisesMessage(IntegrityError, "immutable"), transaction.atomic():
            self.txn.save()
        self.assert_unchanged()

    def test_database_rejects_deletes(self):
        with self.assertRaisesMessage(IntegrityError, "immutable"), transaction.atomic():
            Transaction.objects.filter(pk=self.txn.pk).delete()
        self.assert_unchanged()
