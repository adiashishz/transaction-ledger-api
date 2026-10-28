import uuid
from decimal import Decimal

from django.db import models
from django.db.models import F, Q


# Largest value the balance column (NUMERIC(18,2)) can hold.
MAX_BALANCE = Decimal("9999999999999999.99")


class Account(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    balance = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00"), db_default=Decimal("0.00")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(balance__gte=0), name="account_balance_non_negative"),
            models.CheckConstraint(condition=~Q(name__regex=r"^\s*$"), name="account_name_not_blank"),
        ]

    def __str__(self):
        return f"{self.name} ({self.id})"


class TransactionType(models.TextChoices):
    CREDIT = "credit", "Credit"
    DEBIT = "debit", "Debit"


class Transaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # No separate FK index: transaction_history_idx and the unique constraint both start with account_id.
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="transactions", db_index=False)
    type = models.CharField(max_length=6, choices=TransactionType.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    description = models.CharField(max_length=255, blank=True, default="")
    reference_id = models.CharField(max_length=100)
    # Set only on the two legs of a transfer. Indexed by uniq_transfer_leg_type.
    transfer = models.ForeignKey(
        "Transfer", on_delete=models.PROTECT, null=True, blank=True, related_name="legs", db_index=False
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "reference_id"], name="uniq_transaction_account_reference"),
            # A transfer has at most one debit leg and one credit leg (NULLs don't collide).
            models.UniqueConstraint(fields=["transfer", "type"], name="uniq_transfer_leg_type"),
            models.CheckConstraint(condition=Q(type__in=TransactionType.values), name="transaction_type_valid"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="transaction_amount_positive"),
            models.CheckConstraint(condition=~Q(reference_id__regex=r"^\s*$"), name="transaction_reference_not_blank"),
        ]
        indexes = [
            # Serves the history query: one account, newest first.
            models.Index(fields=["account", "-created_at", "-id"], name="transaction_history_idx"),
        ]

    def __str__(self):
        return f"{self.type} {self.amount} on {self.account_id} ({self.reference_id})"


class Transfer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # No separate index on from_account: uniq_transfer_source_reference starts with it.
    from_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="transfers_out", db_index=False)
    to_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="transfers_in")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    description = models.CharField(max_length=255, blank=True, default="")
    reference_id = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["from_account", "reference_id"], name="uniq_transfer_source_reference"),
            models.CheckConstraint(condition=~Q(from_account=F("to_account")), name="transfer_accounts_differ"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="transfer_amount_positive"),
            models.CheckConstraint(condition=~Q(reference_id__regex=r"^\s*$"), name="transfer_reference_not_blank"),
        ]

    def __str__(self):
        return f"{self.amount} from {self.from_account_id} to {self.to_account_id} ({self.reference_id})"
