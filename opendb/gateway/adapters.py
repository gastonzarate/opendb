"""allauth Google login using the same subject mapping as MCP."""

from django.core.exceptions import PermissionDenied

from opendb.users.adapters import SocialAccountAdapter

from .identity import resolve_google_user


class GoogleSocialAccountAdapter(SocialAccountAdapter):
    def can_authenticate_by_email(self, login, email):
        """Never authenticate an unrelated subject by matching email."""
        return False

    def pre_social_login(self, request, sociallogin):
        """Consume only allauth's upstream-validated Google callback data."""
        account = sociallogin.account
        if (
            account.provider != "google"
            or sociallogin.state.get("process") == "connect"
        ):
            msg = "Only Google sign-in is supported; account linking is disabled."
            raise PermissionDenied(msg)
        data = account.extra_data
        subject = data.get("sub", data.get("id"))
        if subject != account.uid:
            msg = "Google subject does not match the authenticated account."
            raise PermissionDenied(msg)
        user = resolve_google_user(
            {
                "sub": subject,
                "email": data.get("email"),
                "email_verified": data.get(
                    "email_verified", data.get("verified_email")
                ),
                "name": data.get("name"),
            }
        )
        sociallogin.user = user
        sociallogin.account = user.socialaccount_set.get(provider="google", uid=subject)
