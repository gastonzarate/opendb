import pytest

from opendb.users._signals import provision_local_signup_receiver
from opendb.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_skips_when_local_login_disabled(settings, monkeypatch):
    settings.OPENDB_LOCAL_LOGIN_ENABLED = False
    calls = []
    monkeypatch.setattr("opendb.gateway.identity.provision_local_signup", calls.append)
    provision_local_signup_receiver(None, None, UserFactory())
    assert calls == []


def test_skips_social_signups(settings, monkeypatch):
    settings.OPENDB_LOCAL_LOGIN_ENABLED = True
    calls = []
    monkeypatch.setattr("opendb.gateway.identity.provision_local_signup", calls.append)
    provision_local_signup_receiver(None, None, UserFactory(), sociallogin=object())
    assert calls == []


def test_provisions_plain_local_signups(settings, monkeypatch):
    settings.OPENDB_LOCAL_LOGIN_ENABLED = True
    calls = []
    monkeypatch.setattr("opendb.gateway.identity.provision_local_signup", calls.append)
    user = UserFactory()
    provision_local_signup_receiver(None, None, user)
    assert calls == [user]
