from django.conf import settings
from django_otp.admin import OTPAdminSite


class SecureAdminSite(OTPAdminSite):
    def has_permission(self, request):
        if not settings.ADMIN_MFA_REQUIRED:
            return request.user.is_active and request.user.is_staff
        return super().has_permission(request)

