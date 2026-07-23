from apps.accounts.models import USER_PLAN_ORGANIZATION_LIMITS


def navbar_account_context(request):
    user = request.user
    if user.is_authenticated and user.is_superuser:
        try:
            from apps.notifications.models import AdminNotification

            active_notification_count = AdminNotification.objects.filter(closed_at__isnull=True).count()
        except Exception:
            active_notification_count = 0
        return {
            "navbar_show_account_badges": False,
            "navbar_active_notification_count": active_notification_count,
            "navbar_customer_notification_count": 0,
        }

    if not user.is_authenticated or user.is_superuser:
        return {
            "navbar_show_account_badges": False,
            "navbar_active_notification_count": 0,
            "navbar_customer_notification_count": 0,
        }

    organization_count = user.organizations.count()
    organization_limit = user.organization_limit()
    utilization_percent = min(100, int((organization_count / organization_limit) * 100)) if organization_limit else 0
    try:
        from apps.notifications.models import CustomerNotification

        customer_notification_count = CustomerNotification.objects.filter(user=user, closed_at__isnull=True).count()
    except Exception:
        customer_notification_count = 0

    return {
        "navbar_show_account_badges": True,
        "navbar_user_display_name": user.get_full_name() or user.username,
        "navbar_user_plan_tier": user.plan_tier,
        "navbar_organization_count": organization_count,
        "navbar_organization_limit": organization_limit,
        "navbar_organization_utilization": utilization_percent,
        "navbar_plan_limits": USER_PLAN_ORGANIZATION_LIMITS,
        "navbar_active_notification_count": 0,
        "navbar_customer_notification_count": customer_notification_count,
    }
