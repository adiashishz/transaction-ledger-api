"""Error types and handlers. Every error response has the same shape:

    {"error": {"code": "...", "message": "...", "details": {...}}}
"""

import logging

from django.http import JsonResponse
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MESSAGE = "An unexpected error occurred."

# Codes for DRF's built-in exceptions that need no special handling.
STATUS_ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    status.HTTP_406_NOT_ACCEPTABLE: "NOT_ACCEPTABLE",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "UNSUPPORTED_MEDIA_TYPE",
}


def error_body(code, message, details=None):
    return {"error": {"code": code, "message": message, "details": details or {}}}


class LedgerError(exceptions.APIException):
    """Base for domain errors. Subclasses set `status_code`, `error_code` and `default_detail`."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "BAD_REQUEST"
    default_detail = "The request could not be processed."

    def __init__(self, message=None, details=None):
        super().__init__(detail=message)
        self.details = details or {}


class InvalidAccountId(LedgerError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "INVALID_ACCOUNT_ID"
    default_detail = "account_id must be a valid UUID."


class AccountNotFound(LedgerError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "ACCOUNT_NOT_FOUND"
    default_detail = "Account not found."


class DuplicateTransaction(LedgerError):
    status_code = status.HTTP_409_CONFLICT
    error_code = "DUPLICATE_TRANSACTION"
    default_detail = "A transaction with this reference_id already exists for this account."


class InsufficientBalance(LedgerError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "INSUFFICIENT_BALANCE"
    default_detail = "Insufficient funds."


def exception_handler(exc, context):
    """DRF exception handler: reshape every error into the shared error body."""
    response = drf_exception_handler(exc, context)

    if response is None:
        logger.error("Unhandled exception in %s", context.get("view").__class__.__name__, exc_info=exc)
        return Response(
            error_body("INTERNAL_ERROR", INTERNAL_ERROR_MESSAGE),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if isinstance(exc, LedgerError):
        response.data = error_body(exc.error_code, str(exc.detail), exc.details)
    elif isinstance(exc, exceptions.ValidationError):
        response.data = error_body("VALIDATION_ERROR", "Request validation failed.", _field_errors(exc.detail))
    elif isinstance(exc, exceptions.ParseError):
        response.data = error_body("MALFORMED_JSON", str(exc.detail))
    else:
        code = STATUS_ERROR_CODES.get(response.status_code, "ERROR")
        response.data = error_body(code, str(response.data.get("detail", "")))
    return response


def _field_errors(detail):
    """Validation details are always `{field: [messages]}`; errors not tied to a field go under `non_field_errors`."""
    if isinstance(detail, dict):
        return detail
    return {api_settings.NON_FIELD_ERRORS_KEY: detail if isinstance(detail, list) else [detail]}


def bad_request_handler(request, exception):
    return JsonResponse(error_body("BAD_REQUEST", "The request could not be processed."), status=400)


def not_found_handler(request, exception):
    return JsonResponse(error_body("NOT_FOUND", "The requested URL was not found."), status=404)


def server_error_handler(request):
    return JsonResponse(error_body("INTERNAL_ERROR", INTERNAL_ERROR_MESSAGE), status=500)
