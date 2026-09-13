import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

pytestmark = pytest.mark.django_db


def claims(**changes):
    return {
        "sub": "google-subject-123",
        "email": "owner@example.com",
        "email_verified": True,
        "name": "Owner",
        **changes,
    }


def test_google_subject_creates_one_user_with_verified_email_and_no_password():
    from opendb.gateway.identity import resolve_google_user

    user = resolve_google_user(claims())
    again = resolve_google_user(claims())
    assert user.pk == again.pk
    assert not user.has_usable_password()
    assert (
        SocialAccount.objects.get(provider="google", uid=claims()["sub"]).user == user
    )
    assert EmailAddress.objects.get(user=user).verified
    assert get_user_model().objects.count() == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"sub": ""},
        {"sub": None},
        {"sub": 123},
        {"sub": " padded "},
        {"email": "bad"},
        {"email": None},
        {"email_verified": False},
        {"email_verified": "false"},
        {"email_verified": 1},
        {"email_verified": None},
    ],
)
def test_invalid_google_identity_is_rejected_without_writes(changes):
    from opendb.gateway.identity import resolve_google_user

    with pytest.raises(PermissionDenied):
        resolve_google_user(claims(**changes))
    assert not get_user_model().objects.exists()


def test_google_tokeninfo_true_string_is_supported():
    from opendb.gateway.identity import resolve_google_user

    assert resolve_google_user(claims(email_verified="true")).pk


@pytest.mark.parametrize("existing_email", ["owner@example.com", "OWNER@example.com"])
def test_email_collision_never_links_an_unrelated_account(existing_email):
    from opendb.gateway.identity import resolve_google_user

    existing = get_user_model().objects.create_user(email=existing_email)
    with pytest.raises(PermissionDenied):
        resolve_google_user(claims())
    assert not SocialAccount.objects.filter(user=existing).exists()
    assert get_user_model().objects.count() == 1


def test_new_subject_cannot_take_over_another_google_subject_by_email():
    from opendb.gateway.identity import resolve_google_user

    resolve_google_user(claims())
    with pytest.raises(PermissionDenied):
        resolve_google_user(claims(sub="different-google-subject"))
    assert SocialAccount.objects.count() == 1


def test_subject_remains_identity_when_google_email_changes():
    from opendb.gateway.identity import resolve_google_user

    user = resolve_google_user(claims())
    assert resolve_google_user(claims(email="changed@example.com")).pk == user.pk
    user.refresh_from_db()
    assert user.email == "owner@example.com"


def test_inactive_user_cannot_authenticate():
    from opendb.gateway.identity import resolve_google_user

    user = resolve_google_user(claims())
    user.is_active = False
    user.save()
    with pytest.raises(PermissionDenied):
        resolve_google_user(claims())


def test_registration_setting_is_respected_for_new_users(settings):
    from opendb.gateway.identity import resolve_google_user

    settings.ACCOUNT_ALLOW_REGISTRATION = False
    with pytest.raises(PermissionDenied):
        resolve_google_user(claims())


def test_existing_subject_can_login_when_registration_is_closed(settings):
    from opendb.gateway.identity import resolve_google_user

    user = resolve_google_user(claims())
    settings.ACCOUNT_ALLOW_REGISTRATION = False
    assert resolve_google_user(claims()).pk == user.pk
