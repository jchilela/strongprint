import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from common.storage import ensure_bucket, s3_client


@pytest.fixture
def stubbed(settings):
    settings.S3_ENDPOINT_URL = "http://s3.test:8333"
    client = s3_client()
    with Stubber(client) as stub:
        yield client, stub


def test_ensure_bucket_existing(stubbed):
    client, stub = stubbed
    stub.add_response("head_bucket", {}, {"Bucket": "b"})
    assert ensure_bucket(client, "b") is False


def test_ensure_bucket_creates_when_missing(stubbed):
    client, stub = stubbed
    stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stub.add_response("create_bucket", {}, {"Bucket": "b"})
    assert ensure_bucket(client, "b") is True


def test_ensure_bucket_propagates_other_errors(stubbed):
    client, stub = stubbed
    stub.add_client_error("head_bucket", service_error_code="403", http_status_code=403)
    with pytest.raises(ClientError):
        ensure_bucket(client, "b")


def test_public_client_uses_public_endpoint(settings):
    settings.S3_PUBLIC_ENDPOINT_URL = "http://localhost:8333"
    assert s3_client(public=True).meta.endpoint_url == "http://localhost:8333"
