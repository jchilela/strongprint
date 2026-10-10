import structlog
from celery import shared_task

from domain.payments.provider import PaymentProviderError

from . import services

log = structlog.get_logger(__name__)


@shared_task(queue="default")
def reconcile_topups() -> int:
    try:
        changed = services.reconcile_pending()
    except PaymentProviderError as exc:
        log.warning("topups.reconcile_failed", reason=str(exc))
        return 0
    if changed:
        log.info("topups.reconciled", changed=changed)
    return changed
