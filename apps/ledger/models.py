import uuid
from decimal import Decimal

from django.db import models
from django.db.models import Q


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
