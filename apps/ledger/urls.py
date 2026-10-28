from django.urls import path

from apps.ledger.views import AccountCreateView, AccountDetailView, AccountTransactionsView

urlpatterns = [
    path("accounts", AccountCreateView.as_view(), name="account-create"),
    path("accounts/<str:account_id>", AccountDetailView.as_view(), name="account-detail"),
    path("accounts/<str:account_id>/transactions", AccountTransactionsView.as_view(), name="account-transactions"),
]
