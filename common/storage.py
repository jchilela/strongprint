"""Acesso ao armazenamento S3 (SeaweedFS em dev, qualquer S3 em produção)."""

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from django.conf import settings


def s3_client(public: bool = False):
    """Cliente S3. ``public=True`` usa o endpoint visível pelos clientes, para
    gerar URLs pré-assinados que funcionem fora da rede Docker."""
    endpoint = settings.S3_PUBLIC_ENDPOINT_URL if public else settings.S3_ENDPOINT_URL
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket(client=None, bucket: str | None = None) -> bool:
    """Cria o bucket se não existir. Devolve True se foi criado."""
    client = client or s3_client()
    bucket = bucket or settings.S3_BUCKET
    try:
        client.head_bucket(Bucket=bucket)
        return False
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
            raise
    client.create_bucket(Bucket=bucket)
    return True
