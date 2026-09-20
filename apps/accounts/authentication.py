from django.conf import settings
from rest_framework.authentication import TokenAuthentication, SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed


class ProtectedTokenAuthentication(TokenAuthentication):
    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if settings.ADMIN_MFA_REQUIRED and (user.is_superuser or user.is_staff):
            raise AuthenticationFailed("Administrator API access requires an MFA-verified browser session.")
        return user, token


class ProtectedSessionAuthentication(SessionAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            user, _ = result
            if settings.ADMIN_MFA_REQUIRED and (user.is_superuser or user.is_staff) and not user.is_verified():
                raise AuthenticationFailed("Administrator MFA verification required.")
        return result
