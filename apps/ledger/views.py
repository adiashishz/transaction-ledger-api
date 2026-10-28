import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ledger import services
from apps.ledger.exceptions import InvalidAccountId
from apps.ledger.management.commands.seed import ACCOUNTS as SEEDED_ACCOUNTS
from apps.ledger.management.commands.seed import PRIYA, ROHAN
from apps.ledger.pagination import paginate
from apps.ledger.serializers import (
    AccountCreateSerializer,
    AccountPageSerializer,
    AccountSerializer,
    BalanceSerializer,
    ErrorResponseSerializer,
    HistoryQuerySerializer,
    PageQuerySerializer,
    TransactionCreateSerializer,
    TransactionPageSerializer,
    TransactionSerializer,
    TransferCreateSerializer,
    TransferSerializer,
)

# Swagger "Try it out" offers the seeded accounts (`python manage.py seed`) as ready-made ids.
ACCOUNT_ID_PARAMETER = OpenApiParameter(
    "account_id",
    OpenApiTypes.UUID,
    OpenApiParameter.PATH,
    examples=[OpenApiExample(f"{name} (seeded)", value=account_id) for account_id, name in SEEDED_ACCOUNTS],
)
TRANSACTION_EXAMPLES = [
    OpenApiExample(
        "Credit",
        request_only=True,
        value={"type": "credit", "amount": "500.00", "reference_id": "INV-1001", "description": "Invoice 1001"},
    ),
    OpenApiExample("Debit", request_only=True, value={"type": "debit", "amount": "120.50", "reference_id": "PAY-2001"}),
]
TRANSFER_EXAMPLE = OpenApiExample(
    "Priya → Rohan (seeded)",
    request_only=True,
    value={
        "from_account_id": PRIYA,
        "to_account_id": ROHAN,
        "amount": "25.00",
        "reference_id": "TRF-1001",
        "description": "Lunch split",
    },
)


def parse_account_id(raw):
    """Path ids are matched as strings so a malformed id gets a 400 instead of a bare 404."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise InvalidAccountId(details={"account_id": raw})


def validate_query(serializer_class, request):
    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class AccountListCreateView(APIView):
    @extend_schema(
        tags=["Accounts"],
        operation_id="accounts_list",
        summary="List accounts",
        description=(
            "All accounts, newest first, paginated with `page` and `limit`. Not in the brief: added so an "
            "account can be found again when its id wasn't kept from the create response."
        ),
        parameters=[PageQuerySerializer],
        responses={200: AccountPageSerializer, 400: ErrorResponseSerializer},
    )
    def get(self, request):
        query = validate_query(PageQuerySerializer, request)
        accounts, pagination = paginate(services.list_accounts(), query["page"], query["limit"])
        return Response(AccountPageSerializer({"data": accounts, "pagination": pagination}).data)

    @extend_schema(
        tags=["Accounts"],
        summary="Create an account",
        description="Creates an account with a zero balance. Only `name` may be sent.",
        request=AccountCreateSerializer,
        examples=[OpenApiExample("New account", request_only=True, value={"name": "Kavya Nair"})],
        responses={201: AccountSerializer, 400: ErrorResponseSerializer, 415: ErrorResponseSerializer},
    )
    def post(self, request):
        serializer = AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = services.create_account(**serializer.validated_data)
        return Response(AccountSerializer(account).data, status=status.HTTP_201_CREATED)


class AccountDetailView(APIView):
    @extend_schema(
        tags=["Accounts"],
        summary="Get an account",
        description=(
            "The full account, including `name` and `created_at`. Not in the brief: the balance endpoint "
            "returns only `account_id` and `balance`, so this is how a client confirms whose account an id is."
        ),
        parameters=[ACCOUNT_ID_PARAMETER],
        responses={200: AccountSerializer, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
    )
    def get(self, request, account_id):
        account = services.get_account(parse_account_id(account_id))
        return Response(AccountSerializer(account).data)


class AccountBalanceView(APIView):
    @extend_schema(
        tags=["Accounts"],
        summary="Get an account's balance",
        parameters=[ACCOUNT_ID_PARAMETER],
        responses={200: BalanceSerializer, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
    )
    def get(self, request, account_id):
        account = services.get_account(parse_account_id(account_id))
        return Response(BalanceSerializer(account).data)


class AccountTransactionsView(APIView):
    @extend_schema(
        tags=["Transactions"],
        operation_id="accounts_transactions_list",
        summary="List an account's transactions",
        description="Newest first. Filter with `type`; paginate with `page` and `limit`.",
        parameters=[ACCOUNT_ID_PARAMETER, HistoryQuerySerializer],
        responses={200: TransactionPageSerializer, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
    )
    def get(self, request, account_id):
        account_id = parse_account_id(account_id)
        query = validate_query(HistoryQuerySerializer, request)
        transactions = services.list_transactions(account_id, query.get("type"))
        items, pagination = paginate(transactions, query["page"], query["limit"])
        return Response(TransactionPageSerializer({"data": items, "pagination": pagination}).data)

    @extend_schema(
        tags=["Transactions"],
        summary="Add a credit or debit",
        description=(
            "Credits add to the balance; debits subtract and are rejected (422) if the balance is too low. "
            "`reference_id` is unique per account: resending one returns 409 with the original transaction "
            "and changes nothing."
        ),
        parameters=[ACCOUNT_ID_PARAMETER],
        request=TransactionCreateSerializer,
        examples=TRANSACTION_EXAMPLES,
        responses={
            201: TransactionSerializer,
            400: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            409: ErrorResponseSerializer,
            415: ErrorResponseSerializer,
            422: ErrorResponseSerializer,
        },
    )
    def post(self, request, account_id):
        account_id = parse_account_id(account_id)
        serializer = TransactionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        txn = services.post_transaction(
            account_id,
            transaction_type=data["type"],
            amount=data["amount"],
            reference_id=data["reference_id"],
            description=data["description"],
        )
        return Response(TransactionSerializer(txn).data, status=status.HTTP_201_CREATED)


class TransferCreateView(APIView):
    @extend_schema(
        tags=["Transfers"],
        summary="Transfer money between accounts",
        description=(
            "Debits the source and credits the destination as one atomic operation: either both "
            "happen or neither does. Each side is recorded as a transaction (`debit_transaction`, "
            "`credit_transaction`) that also appears in that account's history. `reference_id` is "
            "unique per source account, and must not already be used on either account."
        ),
        request=TransferCreateSerializer,
        examples=[TRANSFER_EXAMPLE],
        responses={
            201: TransferSerializer,
            400: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            409: ErrorResponseSerializer,
            415: ErrorResponseSerializer,
            422: ErrorResponseSerializer,
        },
    )
    def post(self, request):
        serializer = TransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        transfer = services.create_transfer(**serializer.validated_data)
        return Response(TransferSerializer(transfer).data, status=status.HTTP_201_CREATED)
