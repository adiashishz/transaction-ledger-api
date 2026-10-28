"""Business rules for the ledger. Views call these; they never touch models directly."""

from django.db import IntegrityError, transaction
from django.db.models import F

from apps.ledger.exceptions import (
    AccountNotFound,
    BalanceLimitExceeded,
    DuplicateTransaction,
    DuplicateTransfer,
    InsufficientBalance,
)
from apps.ledger.models import MAX_BALANCE, Account, Transaction, TransactionType, Transfer
from apps.ledger.serializers import TransactionSerializer, TransferSerializer

UNIQUE_REFERENCE_CONSTRAINT = "uniq_transaction_account_reference"
UNIQUE_TRANSFER_REFERENCE_CONSTRAINT = "uniq_transfer_source_reference"


def create_account(name):
    return Account.objects.create(name=name)


def list_accounts():
    return Account.objects.order_by("-created_at", "-id")


def get_account(account_id, lock=False):
    """Fetch an account. `lock=True` takes a row lock (SELECT ... FOR UPDATE); it must run inside a transaction."""
    account = _lock_row(account_id) if lock else Account.objects.filter(pk=account_id).first()
    if account is None:
        raise AccountNotFound(details={"account_id": str(account_id)})
    return account


def list_transactions(account_id, transaction_type=None):
    """An account's transactions, newest first. `id` breaks created_at ties so pages stay stable."""
    transactions = get_account(account_id).transactions.order_by("-created_at", "-id")
    if transaction_type is not None:
        transactions = transactions.filter(type=transaction_type)
    return transactions


def post_transaction(account_id, transaction_type, amount, reference_id, description=""):
    """Record a credit or debit and update the balance as one atomic operation.

    The account row stays locked until commit, so concurrent requests for the same
    account run one at a time: two debits can't both pass the balance check, and two
    requests with the same reference_id can't both insert.
    """
    try:
        with transaction.atomic():
            account = get_account(account_id, lock=True)

            existing = _find_transaction(account, reference_id)
            if existing is not None:
                raise _duplicate(existing)

            if transaction_type == TransactionType.DEBIT:
                _check_can_debit(account, amount)
            else:
                _check_can_credit(account, amount)

            txn = Transaction.objects.create(
                account=account,
                type=transaction_type,
                amount=amount,
                reference_id=reference_id,
                description=description,
            )
            delta = amount if transaction_type == TransactionType.CREDIT else -amount
            _apply_balance_change(account, delta)
    except IntegrityError as exc:
        # Backstop: the unique constraint catches a duplicate the lookup above missed.
        if _violated_constraint(exc) != UNIQUE_REFERENCE_CONSTRAINT:
            raise
        raise _duplicate(Transaction.objects.get(account_id=account_id, reference_id=reference_id)) from exc
    return txn


def create_transfer(from_account_id, to_account_id, amount, reference_id, description=""):
    """Move money between two accounts as one atomic operation: a transfer record, a debit
    leg on the source, a credit leg on the destination and both balance updates either all
    commit or none do, so a partial transfer is impossible.

    Both account rows are locked in sorted id order. Every transfer locks in the same order,
    so two transfers in opposite directions (A→B and B→A) queue up instead of deadlocking.
    """
    try:
        with transaction.atomic():
            locked = {}
            for account_id in sorted({from_account_id, to_account_id}):
                locked[account_id] = _lock_row(account_id)
            source, destination = locked[from_account_id], locked[to_account_id]
            if source is None:
                raise _transfer_account_not_found("Source", "from_account_id", from_account_id)
            if destination is None:
                raise _transfer_account_not_found("Destination", "to_account_id", to_account_id)

            existing = source.transfers_out.filter(reference_id=reference_id).first()
            if existing is not None:
                raise _duplicate_transfer(existing)
            # Both legs carry the transfer's reference_id, so it must be free on both accounts.
            for account, role in ((source, "source"), (destination, "destination")):
                existing_leg = _find_transaction(account, reference_id)
                if existing_leg is not None:
                    raise _duplicate(existing_leg, f"the {role} account")

            _check_can_debit(source, amount)
            _check_can_credit(destination, amount)

            transfer = Transfer.objects.create(
                from_account=source,
                to_account=destination,
                amount=amount,
                reference_id=reference_id,
                description=description,
            )
            legs = [
                (source, TransactionType.DEBIT, f"Transfer to {destination.name}"),
                (destination, TransactionType.CREDIT, f"Transfer from {source.name}"),
            ]
            for account, leg_type, default_description in legs:
                Transaction.objects.create(
                    account=account,
                    type=leg_type,
                    amount=amount,
                    reference_id=reference_id,
                    description=description or default_description,
                    transfer=transfer,
                )
            _apply_balance_change(source, -amount)
            _apply_balance_change(destination, amount)
    except IntegrityError as exc:
        # Backstop: unique constraints catch duplicates the lookups above missed.
        constraint = _violated_constraint(exc)
        if constraint == UNIQUE_TRANSFER_REFERENCE_CONSTRAINT:
            existing = Transfer.objects.get(from_account_id=from_account_id, reference_id=reference_id)
            raise _duplicate_transfer(existing) from exc
        if constraint == UNIQUE_REFERENCE_CONSTRAINT:
            for account_id, role in ((from_account_id, "source"), (to_account_id, "destination")):
                existing_leg = Transaction.objects.filter(account_id=account_id, reference_id=reference_id).first()
                if existing_leg is not None:
                    raise _duplicate(existing_leg, f"the {role} account") from exc
        raise
    return transfer


def _lock_row(account_id):
    """SELECT ... FOR UPDATE on one account; None if it doesn't exist."""
    return Account.objects.select_for_update().filter(pk=account_id).first()


def _find_transaction(account, reference_id):
    return account.transactions.filter(reference_id=reference_id).first()


def _check_can_debit(account, amount):
    if account.balance < amount:
        raise InsufficientBalance(
            details={
                "balance": f"{account.balance:.2f}",
                "requested": f"{amount:.2f}",
                "shortfall": f"{amount - account.balance:.2f}",
            },
        )


def _check_can_credit(account, amount):
    if account.balance + amount > MAX_BALANCE:
        raise BalanceLimitExceeded(
            details={
                "balance": f"{account.balance:.2f}",
                "requested": f"{amount:.2f}",
                "max_balance": f"{MAX_BALANCE:.2f}",
            },
        )


def _apply_balance_change(account, delta):
    Account.objects.filter(pk=account.pk).update(balance=F("balance") + delta)


def _transfer_account_not_found(role, field, account_id):
    return AccountNotFound(f"{role} account not found.", details={"field": field, "account_id": str(account_id)})


def _duplicate(existing, owner="this account"):
    return DuplicateTransaction(
        f"A transaction with reference_id '{existing.reference_id}' already exists for {owner}.",
        details={"transaction": TransactionSerializer(existing).data},
    )


def _duplicate_transfer(existing):
    return DuplicateTransfer(
        f"A transfer with reference_id '{existing.reference_id}' already exists for the source account.",
        details={"transfer": TransferSerializer(existing).data},
    )


def _violated_constraint(exc):
    return getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
