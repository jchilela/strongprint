from django.urls import path

from . import views

app_name = "partner"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("entrar/", views.login_phone, name="login"),
    path("entrar/codigo/", views.login_code, name="login_code"),
    path("sair/", views.logout_view, name="logout"),
    path("trocar/", views.switch_partner, name="switch"),
    path("kiosks/<str:code>/", views.kiosk_detail, name="kiosk"),
    path("kiosks/<str:code>/estado/", views.kiosk_status, name="kiosk_status"),
    path("trabalhos/", views.jobs, name="jobs"),
    path("alertas/", views.alerts, name="alerts"),
]
