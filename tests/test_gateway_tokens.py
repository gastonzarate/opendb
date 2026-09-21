import pytest
from django.contrib.auth import get_user_model

from opendb.gateway.tokens import PAT_PREFIX
from opendb.gateway.tokens import generate_personal_access_token
from opendb.gateway.tokens import resolve_personal_access_token
from opendb.users.models import PersonalAccessToken

pytestmark = pytest.mark.django_db


def make_user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email)


def test_generated_token_hashes_the_raw_value_and_keeps_a_display_prefix():
    user = make_user()
    token, raw_token = generate_personal_access_token(user, name="laptop")
    assert raw_token.startswith(PAT_PREFIX)
    assert token.name == "laptop"
    assert token.prefix == raw_token[: len(token.prefix)]
    assert raw_token not in token.token_hash
    assert PersonalAccessToken.objects.filter(pk=token.pk).exists()


def test_resolves_a_valid_token_to_its_owner_and_updates_last_used_at():
    user = make_user()
    token, raw_token = generate_personal_access_token(user)
    assert token.last_used_at is None
    assert resolve_personal_access_token(raw_token) == user.pk
    token.refresh_from_db()
    assert token.last_used_at is not None


def test_rejects_an_unrecognized_or_malformed_token():
    assert resolve_personal_access_token("not-a-token") is None
    assert resolve_personal_access_token("") is None
    assert resolve_personal_access_token(None) is None


def test_rejects_a_revoked_token():
    from django.utils import timezone

    user = make_user()
    token, raw_token = generate_personal_access_token(user)
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])
    assert resolve_personal_access_token(raw_token) is None


def test_rejects_a_token_belonging_to_an_inactive_user():
    user = make_user()
    user.is_active = False
    user.save(update_fields=["is_active"])
    _, raw_token = generate_personal_access_token(user)
    assert resolve_personal_access_token(raw_token) is None


def test_a_tampered_token_does_not_resolve():
    user = make_user()
    _, raw_token = generate_personal_access_token(user)
    assert resolve_personal_access_token(raw_token[:-1] + "x") is None
