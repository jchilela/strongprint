import time

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from common.storage import ensure_bucket


class Command(BaseCommand):
    help = "Cria o bucket S3 dos trabalhos, se ainda não existir (espera pelo S3 até 60 s)."

    def handle(self, *args, **options):
        for _attempt in range(30):
            try:
                created = ensure_bucket()
            except (BotoCoreError, ClientError) as exc:
                last_error = exc
                time.sleep(2)
                continue
            state = "created" if created else "exists"
            self.stdout.write(f"bucket {settings.S3_BUCKET}: {state}")
            return
        raise CommandError(f"S3 unavailable: {last_error}")
