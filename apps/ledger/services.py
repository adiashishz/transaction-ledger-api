"""Business rules for the ledger. Views call these; they never touch models directly."""

from django.db import IntegrityError, transaction
from django.db.models import F

from apps.ledger.exceptions import AccountNotFound, DuplicateTransaction, InsufficientBalance
from apps.ledger.models import Account, Transaction, TransactionType
from apps.ledger.serializers import TransactionSerializer

UNIQUE_REFERENCE_CONSTRAINT = "uniq_transaction_account_reference"


def create_account(name):
    return Account.objects.create(name=name)


def get_account(account_id, lock=False):
    """Fetch an account. `lock=True` takes a row lock (SELECT ... FOR UPDATE); it must run inside a transaction."""
    accounts = Account.objects.select_for_update() if lock else Account.objects
    account = accounts.filter(pk=account_id).first()
    if account is None:
        raise AccountNotFound(details={"account_id": str(account_id)})
    return account


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

            if transaction_type == TransactionType.DEBIT and account.balance < amount:
                raise _insufficient_balance(account.balance, amount)

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


def _find_transaction(account, reference_id):
    return account.transactions.filter(reference_id=reference_id).first()


def _apply_balance_change(account, delta):
    Account.objects.filter(pk=account.pk).update(balance=F("balance") + delta)


def _insufficient_balance(balance, amount):
    return InsufficientBalance(
        details={"balance": f"{balance:.2f}", "requested": f"{amount:.2f}", "shortfall": f"{amount - balance:.2f}"},
    )


def _duplicate(existing):
    return DuplicateTransaction(
        f"A transaction with reference_id '{existing.reference_id}' already exists for this account.",
        details={"transaction": TransactionSerializer(existing).data},
    )


def _violated_constraint(exc):
    return getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
