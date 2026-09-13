"""Resolve identities already validated by Google's OAuth transport.

Never call this with an HTTP payload or an unverified JWT. The stable identity
key is allauth's (provider='google', uid=sub), never an email address.
"""

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.db import transaction

MAX_GOOGLE_SUBJECT_LENGTH = 255
MAX_EMAIL_LENGTH = 254


def validate_google_claims(claims):
    """Validate the identity fields of an upstream-verified Google token."""
    subject = claims.get("sub")
    email = claims.get("email")
    verified = claims.get("email_verified")
    if (
        not isinstance(subject, str)
        or not subject
        or len(subject) > MAX_GOOGLE_SUBJECT_LENGTH
        or any(character.isspace() for character in subject)
        or not isinstance(email, str)
        or len(email) > MAX_EMAIL_LENGTH
        or not (verified is True or verified == "true")
    ):
        msg = "A verified Google identity is required."
        raise PermissionDenied(msg)
    try:
        validate_email(email)
    except ValidationError as exc:
        msg = "A valid verified Google email is required."
        raise PermissionDenied(msg) from exc
    return subject, email.lower()


def _active_user(account):
    if not account.user.is_active:
        msg = "This account is disabled."
        raise PermissionDenied(msg)
    return account.user


def resolve_google_user(claims):
    """Find or atomically create a user by verified Google subject.

    Existing users keep their email; email changes require a separate explicit
    account workflow. A conflicting address never links two identities.
    """
    subject, email = validate_google_claims(claims)
    accounts = SocialAccount.objects.select_related("user")
    account = accounts.filter(provider="google", uid=subject).first()
    if account:
        return _active_user(account)
    if not getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True):
        msg = "Account registration is closed."
        raise PermissionDenied(msg)
    users = get_user_model().objects
    try:
        with transaction.atomic():
            if (
                users.filter(email__iexact=email).exists()
                or EmailAddress.objects.filter(email__iexact=email).exists()
            ):
                msg = "This Google identity cannot be linked automatically."
                raise PermissionDenied(msg)
            name = claims.get("name")
            user = users.create_user(
                email=email,
                password=None,
                name=name[:255] if isinstance(name, str) else "",
            )
            SocialAccount.objects.create(
                user=user,
                provider="google",
                uid=subject,
                extra_data={"sub": subject, "email": email, "email_verified": True},
            )
            EmailAddress.objects.create(
                user=user, email=email, verified=True, primary=True
            )
            return user
    except IntegrityError as exc:
        # A concurrent login may have created this exact subject. The failed
        # atomic block rolls back our candidate user before looking it up.
        account = accounts.filter(provider="google", uid=subject).first()
        if account:
            return _active_user(account)
        msg = "This Google identity cannot be linked automatically."
        raise PermissionDenied(msg) from exc
