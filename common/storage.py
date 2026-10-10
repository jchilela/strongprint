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


def upload_file(path, key: str, content_type: str = "application/octet-stream") -> None:
    s3_client().upload_file(
        str(path), settings.S3_BUCKET, key, ExtraArgs={"ContentType": content_type}
    )


def upload_fileobj(fileobj, key: str, content_type: str = "application/octet-stream") -> None:
    s3_client().upload_fileobj(
        fileobj, settings.S3_BUCKET, key, ExtraArgs={"ContentType": content_type}
    )


def download_file(key: str, path) -> None:
    s3_client().download_file(settings.S3_BUCKET, key, str(path))


def object_size(key: str) -> int | None:
    """Tamanho do objeto, ou None se não existir."""
    try:
        head = s3_client().head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return int(head["ContentLength"])


def delete_prefix(prefix: str) -> int:
    client = s3_client()
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.S3_BUCKET, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=settings.S3_BUCKET, Delete={"Objects": keys})
            deleted += len(keys)
    return deleted


def presigned_put(key: str, content_type: str, expires: int = 900) -> str | None:
    """URL para o cliente enviar o ficheiro diretamente ao S3 (se houver endpoint público)."""
    if not settings.S3_PUBLIC_ENDPOINT_URL:
        return None
    return s3_client(public=True).generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
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
