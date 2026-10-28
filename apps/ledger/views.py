import uuid

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ledger import services
from apps.ledger.exceptions import InvalidAccountId
from apps.ledger.serializers import AccountCreateSerializer, AccountSerializer, ErrorResponseSerializer

ACCOUNT_ID_PARAMETER = OpenApiParameter("account_id", OpenApiTypes.UUID, OpenApiParameter.PATH)


def parse_account_id(raw):
    """Path ids are matched as strings so a malformed id gets a 400 instead of a bare 404."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise InvalidAccountId(details={"account_id": raw})


class AccountCreateView(APIView):
    @extend_schema(
        tags=["Accounts"],
        summary="Create an account",
        description="Creates an account with a zero balance. Only `name` may be sent.",
        request=AccountCreateSerializer,
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
        parameters=[ACCOUNT_ID_PARAMETER],
        responses={200: AccountSerializer, 400: ErrorResponseSerializer, 404: ErrorResponseSerializer},
    )
    def get(self, request, account_id):
        account = services.get_account(parse_account_id(account_id))
        return Response(AccountSerializer(account).data)
