from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from .views import needs_name

ALLOWED = {"portal:profile_name", "portal:logout", "set_language"}


class NameRequiredMiddleware:
    """Enquanto o cliente não tiver nome, só pode guardá-lo (ou sair).

    A janela obrigatória cobre todas as páginas do portal; isto garante o mesmo
    do lado do servidor para qualquer pedido que altere dados.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        match = request.resolver_match
        if match is None or match.namespace != "portal" or match.view_name in ALLOWED:
            return None
        if needs_name(request.user):
            messages.error(request, _("Please enter your name to continue."))
            return redirect("portal:home")
        return None
