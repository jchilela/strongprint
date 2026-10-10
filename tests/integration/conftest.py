import pytest
from moto import mock_aws
from rest_framework.test import APIClient

from common.storage import ensure_bucket
from tests.factories import KioskFactory, PrinterFactory, UserFactory


@pytest.fixture
def s3():
    with mock_aws():
        ensure_bucket()
        yield


@pytest.fixture
def kiosk(db):
    kiosk = KioskFactory()
    PrinterFactory(kiosk=kiosk)
    return kiosk


@pytest.fixture
def customer(db):
    return UserFactory()


@pytest.fixture
def api(customer):
    client = APIClient()
    client.force_authenticate(customer)
    return client
