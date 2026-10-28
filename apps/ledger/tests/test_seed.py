from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from rest_framework.test import APITestCase

from apps.ledger import services
from apps.ledger.management.commands.seed import AARAV, ANANYA, PRIYA, ROHAN
from apps.ledger.models import Account, Transaction, Transfer
from apps.ledger.tests.test_integrity import stored_balance

EXPECTED = {
    AARAV: (Decimal("100.00"), 4),
    PRIYA: (Decimal("4495.00"), 31),
    ROHAN: (Decimal("0.00"), 0),
    ANANYA: (Decimal("950.00"), 2),
}


def seed(*args):
    out = StringIO()
    call_command("seed", *args, stdout=out)
    return out.getvalue()


class SeedTests(APITestCase):
    def assert_seeded_state(self):
        for account_id, (balance, transaction_count) in EXPECTED.items():
            account = Account.objects.get(pk=account_id)
            with self.subTest(account=account.name):
                self.assertEqual(account.balance, balance)
                self.assertEqual(account.transactions.count(), transaction_count)
                self.assertEqual(account.balance, stored_balance(account))

    def test_seeds_accounts_transactions_and_transfer(self):
        output = seed()

        self.assert_seeded_state()
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertIn("Seed complete: 40 created, 0 already present.", output)

    def test_aarav_follows_the_brief_example(self):
        seed()
        history = self.client.get(f"/api/accounts/{AARAV}/transactions").json()["data"]
        self.assertEqual(
            [(t["type"], t["amount"]) for t in reversed(history)],
            [("credit", "100.00"), ("credit", "50.00"), ("debit", "30.00"), ("debit", "20.00")],
        )

    def test_priya_has_enough_history_to_page(self):
        seed()
        pagination = self.client.get(f"/api/accounts/{PRIYA}/transactions?limit=10").json()["pagination"]
        self.assertEqual((pagination["total_records"], pagination["total_pages"]), (31, 4))

    def test_running_twice_changes_nothing(self):
        seed()
        output = seed()

        self.assertIn("Seed complete: 0 created, 40 already present.", output)
        self.assert_seeded_state()
        self.assertEqual(Transaction.objects.count(), 37)

    def test_rerun_keeps_changes_made_through_the_api(self):
        seed()
        services.post_transaction(AARAV, "debit", Decimal("10.00"), "MANUAL-1")
        seed()
        self.assertEqual(Account.objects.get(pk=AARAV).balance, Decimal("90.00"))

    def test_reset_deletes_everything_else_and_reseeds(self):
        seed()
        extra = Account.objects.create(name="Someone Else")
        services.post_transaction(extra.id, "credit", Decimal("5.00"), "X-1")
        services.post_transaction(AARAV, "debit", Decimal("10.00"), "MANUAL-1")

        output = seed("--reset")

        self.assertIn("Deleted all accounts, transactions and transfers.", output)
        self.assertFalse(Account.objects.filter(pk=extra.id).exists())
        self.assertEqual(Account.objects.count(), 4)
        self.assert_seeded_state()
