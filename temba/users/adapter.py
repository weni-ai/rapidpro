from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core import context as allauth_context
from allauth.mfa.adapter import DefaultMFAAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.signals import social_account_added

from django.contrib import messages
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.orgs.models import Invitation
from temba.orgs.views.views import switch_to_org
from temba.users.models import User
from temba.utils.email.send import EmailSender


class InviteAdapterMixin:
    def post_login(self, request, user, *, email_verification, signal_kwargs, email, signup, redirect_url):
        # if we are working with an invite, mark it as accepted
        secret = request.session.pop("invite_secret", None)
        if secret:
            invite = Invitation.objects.filter(secret=secret, is_active=True).first()
            if invite:
                # this can happen if a SSO with a different email address is used
                if user.email.lower() != invite.email.lower():
                    messages.add_message(
                        self.request,
                        messages.WARNING,
                        _(f"To accept this invitation, please login with {invite.email}."),
                    )
                else:
                    invite.accept(user)
                    switch_to_org(request, user)

        # DefaultSocialAccountAdapter does not inherit DefaultAccountAdapter, so delegate
        # invite handling here and continue login via the account adapter.
        if isinstance(self, DefaultAccountAdapter):
            return super().post_login(
                request,
                user,
                email_verification=email_verification,
                signal_kwargs=signal_kwargs,
                email=email,
                signup=signup,
                redirect_url=redirect_url,
            )

        from allauth.account.adapter import get_adapter as get_account_adapter

        return get_account_adapter().post_login(
            request,
            user,
            email_verification=email_verification,
            signal_kwargs=signal_kwargs,
            email=email,
            signup=signup,
            redirect_url=redirect_url,
        )

    def is_open_for_signup(self, request, sociallogin=None):
        # if we have a signup invite, we need to allow signups
        secret = request.GET.get("invite", request.session.get("invite_secret", None))

        if secret and Invitation.objects.filter(secret=secret, is_active=True).exists():
            return True

        return "signups" in request.branding.get("features")


class TembaAccountAdapter(InviteAdapterMixin, DefaultAccountAdapter):
    def send_mail(self, template_prefix, email, context):

        # our emails need some additional context
        context["branding"] = self.request.branding
        context["now"] = timezone.now()

        sender = EmailSender.from_email_type(self.request.branding, "notifications")
        sender.send([email], template_prefix, context)


class TembaSocialAccountAdapter(InviteAdapterMixin, DefaultSocialAccountAdapter):

    @staticmethod
    def extract_email(sociallogin):
        for address in getattr(sociallogin, "email_addresses", []) or []:
            if address.email:
                return address.email

        if not hasattr(sociallogin, "account") or not hasattr(sociallogin.account, "extra_data"):
            return None

        extra_data = sociallogin.account.extra_data
        return extra_data.get("email") or extra_data.get("upn") or extra_data.get("preferred_username")

    @staticmethod
    def mark_email_verified(user, email):
        address = EmailAddress.objects.filter(user=user, email__iexact=email).first()
        if address:
            if not address.verified or not address.primary:
                address.verified = True
                address.primary = True
                address.save(update_fields=["verified", "primary"])
        else:
            EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)

    @staticmethod
    def ensure_verified_email_addresses(sociallogin, email):
        sociallogin.email_addresses = [EmailAddress(email=email, verified=True, primary=True)]

    def is_email_verified(self, provider, email):
        # Microsoft Entra is a trusted IdP; treat OIDC emails as verified so allauth
        # lookup() can match existing accounts before auto-signup runs.
        if provider.id == "openid_connect" and email:
            return True
        return super().is_email_verified(provider, email)

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        email = self.extract_email(sociallogin)
        if not user.email:
            user.email = email
        if "email" not in data and email:
            data["email"] = email
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        if user.email:
            self.mark_email_verified(user, user.email)
        return user

    def pre_social_login(self, request, sociallogin):
        email = self.extract_email(sociallogin)
        if not email:
            return

        self.ensure_verified_email_addresses(sociallogin, email)

        sociallogin_user = getattr(sociallogin, "user", None)
        if sociallogin_user is None or sociallogin_user.pk is None:
            user = User.get_by_email(email)
            if user:
                sociallogin.connect(request, user)
                self.mark_email_verified(user, email)


@receiver(social_account_added)
def update_user_profile_picture(request, sociallogin, **kwargs):  # pragma: no cover
    user = sociallogin.user
    try:
        avatar_url = sociallogin.account.get_avatar_url()
    except Exception:
        return
    if avatar_url:
        user.fetch_avatar(avatar_url)


class TembaMFAAdapter(DefaultMFAAdapter):
    def _get_site_name(self) -> str:
        return allauth_context.request.get_host()

    def build_totp_url(self, user, secret: str) -> str:
        url = super().build_totp_url(user, secret)

        # some totp clients support images in the QR code
        url = f"{url}&image={self.request.branding.get("logos").get("favico")}"
        return url
