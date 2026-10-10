from .views import needs_name


def portal(request):
    user = getattr(request, "user", None)
    return {"needs_name": user is not None and needs_name(user)}
