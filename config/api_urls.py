from django.urls import path

from apps.jobs import api as jobs
from apps.payments import api as payments

urlpatterns = [
    path("kiosks/<str:code>/sessions", jobs.kiosk_session, name="kiosk-session"),
    path("kiosks/<str:code>/jobs", jobs.create_job, name="job-create"),
    path("jobs/<uuid:job_id>", jobs.job_detail, name="job-detail"),
    path("jobs/<uuid:job_id>/file", jobs.upload_file, name="job-file"),
    path("jobs/<uuid:job_id>/uploaded", jobs.mark_uploaded, name="job-uploaded"),
    path("jobs/<uuid:job_id>/options", jobs.job_options, name="job-options"),
    path("jobs/<uuid:job_id>/checkout", jobs.job_checkout, name="job-checkout"),
    path("jobs/<uuid:job_id>/cancel", jobs.job_cancel, name="job-cancel"),
    path("wallet", payments.wallet_detail, name="wallet"),
    path("wallet/topups", payments.create_topup, name="topup-create"),
    path("wallet/topups/<uuid:topup_id>", payments.topup_detail, name="topup-detail"),
    path(
        "wallet/topups/<uuid:topup_id>/simulate",
        payments.simulate_topup,
        name="topup-simulate",
    ),
    path("payments/webhook/<str:provider>", payments.payment_webhook, name="payment-webhook"),
]
