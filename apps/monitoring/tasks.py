import structlog
from celery import shared_task

from apps.devices.models import Printer

from . import services

log = structlog.get_logger(__name__)


@shared_task(queue="monitoring")
def poll_printers() -> int:
    """A cada 60 s: estado e contador das impressoras em kiosks ativos."""
    printers = Printer.objects.filter(kiosk__is_active=True).select_related("kiosk")
    count = 0
    for printer in printers:
        structlog.contextvars.bind_contextvars(kiosk=printer.kiosk.code)
        try:
            services.poll_printer(printer)
            count += 1
        except Exception:  # uma impressora com problemas não pára as outras
            log.exception("monitor.poll_failed", printer=printer.name)
        finally:
            structlog.contextvars.unbind_contextvars("kiosk")
    return count


@shared_task(queue="monitoring")
def notify_downtimes() -> int:
    """A cada 60 s: e-mail quando um kiosk fica parado e quando volta."""
    from .notify import notify_downtimes

    return notify_downtimes()


@shared_task(queue="monitoring")
def mark_offline_kiosks() -> int:
    count = services.mark_offline_kiosks()
    services.record_all_availability()
    if count:
        log.warning("monitor.kiosks_offline", count=count)
    return count
