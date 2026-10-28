from django.urls import path

from apps.ledger.views import AccountCreateView, AccountDetailView

urlpatterns = [
    path("accounts", AccountCreateView.as_view(), name="account-create"),
    path("accounts/<str:account_id>", AccountDetailView.as_view(), name="account-detail"),
]
