from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from .models import AdminNotification, CustomerNotification, NotificationCategory
from .services import scan_admin_notifications, scan_customer_notifications


class AdminNotificationsRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)


class AdminNotificationListView(AdminNotificationsRequiredMixin, TemplateView):
    template_name = "notifications/admin_notification_list.html"

    def get_context_data(self, **kwargs):
        scan_admin_notifications()
        context = super().get_context_data(**kwargs)
        show = self.request.GET.get("show", "active")
        selected_type = self.request.GET.get("type", "").strip()
        q = self.request.GET.get("q", "").strip()
        notifications = AdminNotification.objects.select_related("customer", "closed_by")
        if selected_type in NotificationCategory.values:
            notifications = notifications.filter(category=selected_type)
        else:
            selected_type = ""
        if show == "closed":
            notifications = notifications.filter(closed_at__isnull=False)
        elif show == "all":
            pass
        else:
            show = "active"
            notifications = notifications.filter(closed_at__isnull=True)
        if q:
            notifications = notifications.filter(
                title__icontains=q
            ) | notifications.filter(
                message__icontains=q
            ) | notifications.filter(
                customer__email__icontains=q
            )
            notifications = notifications.select_related("customer", "closed_by")
        notification_rows = list(notifications.order_by("closed_at", "-created_at"))
        for notification in notification_rows:
            notification.display_title = notification.localized_title(self.request.LANGUAGE_CODE)
            notification.display_message = notification.localized_message(self.request.LANGUAGE_CODE)
        context["notifications"] = notification_rows
        context["active_count"] = AdminNotification.objects.filter(closed_at__isnull=True).count()
        context["closed_count"] = AdminNotification.objects.filter(closed_at__isnull=False).count()
        context["show"] = show
        context["selected_type"] = selected_type
        context["type_choices"] = NotificationCategory.choices
        context["search_query"] = q
        return context


class AdminNotificationCloseView(AdminNotificationsRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(AdminNotification, pk=pk, closed_at__isnull=True)
        notification.closed_at = timezone.now()
        notification.closed_by = request.user
        notification.save(update_fields=["closed_at", "closed_by", "updated_at"])
        messages.success(request, "Powiadomienie zostało zamknięte.")
        next_url = request.POST.get("next") or reverse("dashboard:notifications")
        return redirect(next_url)


class CustomerNotificationListView(LoginRequiredMixin, TemplateView):
    template_name = "notifications/customer_notification_list.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_superuser:
            return redirect("dashboard:notifications")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        scan_customer_notifications(self.request.user)
        context = super().get_context_data(**kwargs)
        show = self.request.GET.get("show", "active")
        selected_type = self.request.GET.get("type", "").strip()
        q = self.request.GET.get("q", "").strip()
        notifications = CustomerNotification.objects.filter(user=self.request.user)
        if selected_type in NotificationCategory.values:
            notifications = notifications.filter(category=selected_type)
        else:
            selected_type = ""
        if show == "closed":
            notifications = notifications.filter(closed_at__isnull=False)
        elif show == "all":
            pass
        else:
            show = "active"
            notifications = notifications.filter(closed_at__isnull=True)
        if q:
            notifications = notifications.filter(title__icontains=q) | notifications.filter(message__icontains=q)
            notifications = notifications.filter(user=self.request.user)
        notification_rows = list(notifications.order_by("closed_at", "-created_at"))
        for notification in notification_rows:
            notification.display_title = notification.localized_title(self.request.LANGUAGE_CODE)
            notification.display_message = notification.localized_message(self.request.LANGUAGE_CODE)
        context["notifications"] = notification_rows
        context["active_count"] = CustomerNotification.objects.filter(user=self.request.user, closed_at__isnull=True).count()
        context["closed_count"] = CustomerNotification.objects.filter(user=self.request.user, closed_at__isnull=False).count()
        context["show"] = show
        context["selected_type"] = selected_type
        context["type_choices"] = NotificationCategory.choices
        context["search_query"] = q
        return context


class CustomerNotificationCloseView(LoginRequiredMixin, View):
    def post(self, request, pk):
        notification = get_object_or_404(CustomerNotification, pk=pk, user=request.user, closed_at__isnull=True)
        notification.closed_at = timezone.now()
        notification.save(update_fields=["closed_at", "updated_at"])
        messages.success(request, "Notification closed.")
        next_url = request.POST.get("next") or reverse("dashboard:customer-notifications")
        return redirect(next_url)
