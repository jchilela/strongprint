import structlog


class LogContextMiddleware:
    """Limpa o contexto dos logs no início de cada pedido (evita job_id de pedidos anteriores)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        structlog.contextvars.clear_contextvars()
        return self.get_response(request)
