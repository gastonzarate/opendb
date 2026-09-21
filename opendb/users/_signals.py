"""allauth signal receivers wired from UsersConfig.ready()."""

from django.conf import settings


def provision_local_signup_receiver(sender, request, user, **kwargs):
    """Provision a personal database after a plain email+password signup.

    Google signups already provision explicitly in resolve_google_user and
    never reach this receiver as a fresh signup, but the sociallogin check
    keeps this receiver a no-op for any social signup regardless.
    """
    if not getattr(settings, "OPENDB_LOCAL_LOGIN_ENABLED", False):
        return
    if kwargs.get("sociallogin") is not None:
        return
    from opendb.gateway.identity import provision_local_signup  # noqa: PLC0415

    provision_local_signup(user)
