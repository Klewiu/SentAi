from django.urls import path
from .google_signin import GoogleSignInView
from .mfa import FriendlyMFALoginView, MFASetupView

from .views import MFALoginView, VerificationNoticeView, VerifyEmailView, ResendVerificationView, AccountCloseView, ProfilePasswordChangeView, ProfileView

app_name = "accounts"

urlpatterns = [
    path("google/sign-in/", GoogleSignInView.as_view(), name="google-signin"),
    path("mfa/", FriendlyMFALoginView.as_view(), name="mfa"),
    path("mfa/setup/", MFASetupView.as_view(), name="mfa-setup"),
    path("verify-email/", VerificationNoticeView.as_view(), name="verify-notice"),
    path("verify-email/<str:token>/", VerifyEmailView.as_view(), name="verify-email"),
    path("resend-verification/", ResendVerificationView.as_view(), name="resend-verification"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/password/", ProfilePasswordChangeView.as_view(), name="profile-password"),
    path("profile/close-account/", AccountCloseView.as_view(), name="close-account"),
]
