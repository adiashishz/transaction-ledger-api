from django.urls import path

from apps.ledger.views import (
    AccountBalanceView,
    AccountDetailView,
    AccountListCreateView,
    AccountTransactionsView,
    TransferCreateView,
)

urlpatterns = [
    path("accounts", AccountListCreateView.as_view(), name="account-list"),
    path("accounts/<str:account_id>", AccountDetailView.as_view(), name="account-detail"),
    path("accounts/<str:account_id>/balance", AccountBalanceView.as_view(), name="account-balance"),
    path("accounts/<str:account_id>/transactions", AccountTransactionsView.as_view(), name="account-transactions"),
    path("transfers", TransferCreateView.as_view(), name="transfer-create"),
]
