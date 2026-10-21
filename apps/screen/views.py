from django.conf import settings
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_deny


@xframe_options_deny
def screen(request):
    """Aplicação do ecrã do kiosk. O token do Pi chega no fragmento do URL
    (#token=...), que nunca é enviado ao servidor; o JavaScript guarda-o."""
    return render(
        request,
        "screen/index.html",
        {
            "agent_url": settings.KIOSK_AGENT_URL,
            "idle_seconds": settings.KIOSK_IDLE_SECONDS,
            "topup_min": settings.TOPUP_MIN_KZ,
        },
    )
