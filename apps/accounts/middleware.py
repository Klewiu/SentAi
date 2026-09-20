import hashlib
import time
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from .models import AuthRateWindow


class AccountSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and any(part in request.path for part in ("/login/", "/register/", "/resend-verification/", "/password_reset/", "/mfa/", "/google/sign-in/")):
            # REMOTE_ADDR must be set by a trusted proxy. Never trust arbitrary XFF.
            identity = request.META.get("REMOTE_ADDR", "unknown")
            key = hashlib.sha256(identity.encode()).hexdigest()
            with transaction.atomic():
                row, _ = AuthRateWindow.objects.get_or_create(key=key, window=int(time.time()) // 900)
                AuthRateWindow.objects.filter(pk=row.pk).update(count=F("count") + 1)
                row.refresh_from_db()
                exceeded = row.count > settings.AUTH_RATE_LIMIT
            if exceeded:
                response = HttpResponse("Too many attempts. Please try again later.", status=429)
                response["Retry-After"] = "900"
                return response
        if request.user.is_authenticated:
            if settings.ADMIN_MFA_REQUIRED and (request.user.is_superuser or request.user.is_staff) and not request.user.is_verified():
                from django.urls import reverse
                from django.shortcuts import redirect
                if request.path not in {reverse("accounts:mfa"), reverse("accounts:mfa-setup"), reverse("logout")}:
                    return redirect("accounts:mfa")
            from apps.billing.access import reconcile_access
            reconcile_access(request.user)
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        return response
