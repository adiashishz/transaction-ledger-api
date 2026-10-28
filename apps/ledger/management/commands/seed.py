"""Load demo accounts, transactions and a transfer for trying the API.

    python manage.py seed           # create whatever is missing; safe to run again
    python manage.py seed --reset   # delete ALL ledger data first, then seed

Everything goes through the service layer, so seeded data obeys the same rules as API
requests. Re-running is harmless: every seeded transaction has a fixed reference_id, so
anything already present is reported as a duplicate and skipped.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.ledger import services
from apps.ledger.exceptions import DuplicateTransaction, DuplicateTransfer
from apps.ledger.models import Account, Transaction, Transfer

AARAV = "00000000-0000-4000-8000-000000000001"
PRIYA = "00000000-0000-4000-8000-000000000002"
ROHAN = "00000000-0000-4000-8000-000000000003"
ANANYA = "00000000-0000-4000-8000-000000000004"

ACCOUNTS = [
    (AARAV, "Aarav Sharma"),  # the brief's example: +100 +50 −30 −20 = 100.00
    (PRIYA, "Priya Patel"),  # 30 transactions plus a transfer out, for paging and filtering
    (ROHAN, "Rohan Mehta"),  # empty account, for trying an insufficient-balance debit
    (ANANYA, "Ananya Iyer"),  # receives the transfer from Priya
]

PRIYA_SPENDING = ["Groceries", "Fuel", "Dining out", "Shopping"]


def seed_transactions():
    """(account_id, type, amount, reference_id, description), oldest first."""
    yield AARAV, "credit", "100.00", "SEED-001", "Opening deposit"
    yield AARAV, "credit", "50.00", "SEED-002", "Salary"
    yield AARAV, "debit", "30.00", "SEED-003", "Groceries"
    yield AARAV, "debit", "20.00", "SEED-004", "Electricity bill"

    yield PRIYA, "credit", "5000.00", "SEED-101", "Opening deposit"
    for n in range(2, 31):
        if n % 3 == 0:
            yield PRIYA, "credit", "250.00", f"SEED-{100 + n}", "Freelance payment"
        else:
            yield PRIYA, "debit", f"{40 + 5 * n}.00", f"SEED-{100 + n}", PRIYA_SPENDING[n % 4]

    yield ANANYA, "credit", "200.00", "SEED-301", "Opening deposit"


SEED_TRANSFERS = [
    # (from_account_id, to_account_id, amount, reference_id, description)
    (PRIYA, ANANYA, "750.00", "SEED-TRF-001", "Rent share"),
]


class Command(BaseCommand):
    help = "Load demo accounts, transactions and a transfer. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete ALL ledger data before seeding.")

    def handle(self, *args, reset=False, **options):
        with transaction.atomic():
            if reset:
                self.delete_ledger_data()
            created, skipped = self.seed()

        self.stdout.write(self.style.SUCCESS(f"Seed complete: {created} created, {skipped} already present."))
        self.print_accounts()

    def delete_ledger_data(self):
        # TRUNCATE skips the row-level immutability triggers, which only guard UPDATE and DELETE.
        # Postgres refuses to TRUNCATE while deferred foreign-key checks are pending, so run them first.
        tables = ", ".join(model._meta.db_table for model in (Transaction, Transfer, Account))
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(f"TRUNCATE TABLE {tables}")
        self.stdout.write(self.style.WARNING("Deleted all accounts, transactions and transfers."))

    def seed(self):
        created = skipped = 0
        for account_id, name in ACCOUNTS:
            _, was_created = Account.objects.get_or_create(id=account_id, defaults={"name": name})
            created, skipped = created + was_created, skipped + (not was_created)

        for account_id, transaction_type, amount, reference_id, description in seed_transactions():
            try:
                services.post_transaction(account_id, transaction_type, Decimal(amount), reference_id, description)
                created += 1
            except DuplicateTransaction:
                skipped += 1

        for from_id, to_id, amount, reference_id, description in SEED_TRANSFERS:
            try:
                services.create_transfer(from_id, to_id, Decimal(amount), reference_id, description)
                created += 1
            except DuplicateTransfer:
                skipped += 1
        return created, skipped

    def print_accounts(self):
        self.stdout.write(f"\n  {'Account':<14} {'ID':<38} {'Balance':>10}  Transactions")
        for account_id, _ in ACCOUNTS:
            account = Account.objects.get(pk=account_id)
            count = account.transactions.count()
            self.stdout.write(f"  {account.name:<14} {account_id:<38} {account.balance:>10}  {count:>12}")
