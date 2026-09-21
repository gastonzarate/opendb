import pytest

from opendb.users.adapters import AccountAdapter

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("local_login_enabled", "allow_registration", "expected"),
    [
        (False, True, False),
        (True, False, False),
        (False, False, False),
        (True, True, True),
    ],
)
def test_signup_requires_both_local_login_and_registration_flags(
    rf, settings, local_login_enabled, allow_registration, expected
):
    settings.OPENDB_LOCAL_LOGIN_ENABLED = local_login_enabled
    settings.ACCOUNT_ALLOW_REGISTRATION = allow_registration
    request = rf.get("/accounts/signup/")
    assert AccountAdapter().is_open_for_signup(request) is expected
