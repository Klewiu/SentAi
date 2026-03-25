from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView, TemplateView
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .forms import ProfileForm, ProfilePasswordChangeForm, UserRegistrationForm
from .serializers import CurrentUserSerializer, TokenLoginSerializer


class RegisterView(FormView):
    template_name = "registration/register.html"
    form_class = UserRegistrationForm
    success_url = reverse_lazy("login")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        return kwargs

    def form_valid(self, form):
        form.save()
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Konto zostało utworzone. Możesz się zalogować.")
        else:
            messages.success(self.request, "Account created. You can now sign in.")
        return super().form_valid(form)


class TokenLoginView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = TokenLoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        login(request, user)
        return Response(
            {
                "token": token.key,
                "user": CurrentUserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return Response(CurrentUserSerializer(request.user).data)


class ProfileView(LoginRequiredMixin, FormView):
    template_name = "registration/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        return kwargs

    def form_valid(self, form):
        form.save()
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Dane zostały zaktualizowane.")
        else:
            messages.success(self.request, "Profile updated successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "password_form" not in context:
            context["password_form"] = ProfilePasswordChangeForm(
                user=self.request.user,
                language_code=self.request.LANGUAGE_CODE,
            )
        return context


class ProfilePasswordChangeView(LoginRequiredMixin, FormView):
    template_name = "registration/profile.html"
    form_class = ProfilePasswordChangeForm
    success_url = reverse_lazy("accounts:profile")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["language_code"] = self.request.LANGUAGE_CODE
        return kwargs

    def form_valid(self, form):
        form.save()
        update_session_auth_hash(self.request, form.user)
        if self.request.LANGUAGE_CODE == "pl":
            messages.success(self.request, "Hasło zostało zmienione.")
        else:
            messages.success(self.request, "Password changed successfully.")
        return super().form_valid(form)

    def form_invalid(self, form):
        profile_form = ProfileForm(
            instance=self.request.user,
            language_code=self.request.LANGUAGE_CODE,
        )
        return self.render_to_response(
            self.get_context_data(form=ProfileForm(
                instance=self.request.user,
                language_code=self.request.LANGUAGE_CODE,
            ), password_form=form)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "form" not in context:
            context["form"] = ProfileForm(
                instance=self.request.user,
                language_code=self.request.LANGUAGE_CODE,
            )
        context["password_form"] = context.pop("form", context.get("password_form"))
        context["form"] = ProfileForm(
            instance=self.request.user,
            language_code=self.request.LANGUAGE_CODE,
        )
        return context
