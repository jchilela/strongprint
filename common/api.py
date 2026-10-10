"""Erros de domínio → respostas JSON estáveis: ``{"error": <código>, "detail": <texto>}``."""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.wallets.services import InsufficientFunds
from domain.analysis.types import AnalysisError
from domain.jobs.state_machine import InvalidTransition
from domain.payments.provider import PaymentProviderError
from domain.pricing.service import InvalidOptions


def error(code: str, detail: str, http_status: int, **extra) -> Response:
    return Response({"error": code, "detail": detail, **extra}, status=http_status)


def exception_handler(exc, context):
    if isinstance(exc, InvalidTransition):
        return error(
            "invalid_state",
            f"Not allowed while the job is {exc.current}",
            status.HTTP_409_CONFLICT,
            state=exc.current,
        )
    if isinstance(exc, InsufficientFunds):
        return error(
            "insufficient_funds",
            "Not enough wallet balance",
            status.HTTP_402_PAYMENT_REQUIRED,
            available_kz=exc.available_kz,
            required_kz=exc.required_kz,
            missing_kz=exc.required_kz - exc.available_kz,
        )
    if isinstance(exc, (InvalidOptions, AnalysisError)):
        return error(exc.code, str(exc), status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, PaymentProviderError):
        return error("payment_provider_error", str(exc), status.HTTP_502_BAD_GATEWAY)
    return drf_exception_handler(exc, context)
