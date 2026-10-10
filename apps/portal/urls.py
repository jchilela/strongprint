from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.home, name="home"),
    path("entrar/", views.login_phone, name="login"),
    path("entrar/codigo/", views.login_code, name="login_code"),
    path("entrar/reenviar/", views.resend_code, name="resend_code"),
    path("perfil/nome/", views.profile_name, name="profile_name"),
    path("sair/", views.logout_view, name="logout"),
    path("carteira/", views.wallet, name="wallet"),
    path("carteira/carregar/", views.topup_new, name="topup_new"),
    path("carteira/carregamentos/<uuid:topup_id>/", views.topup_detail, name="topup"),
    path(
        "carteira/carregamentos/<uuid:topup_id>/simular/",
        views.topup_simulate,
        name="topup_simulate",
    ),
    path("ficheiros/", views.documents, name="documents"),
    path("ficheiros/<uuid:document_id>/apagar/", views.document_delete, name="document_delete"),
    path("impressoes/", views.history, name="history"),
]
