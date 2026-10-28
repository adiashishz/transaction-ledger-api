"""Business rules for the ledger. Views call these; they never touch models directly."""

from apps.ledger.exceptions import AccountNotFound
from apps.ledger.models import Account


def create_account(name):
    return Account.objects.create(name=name)


def get_account(account_id):
    account = Account.objects.filter(pk=account_id).first()
    if account is None:
        raise AccountNotFound(details={"account_id": str(account_id)})
    return account
