from django.contrib.admin.apps import AdminConfig


class SecureAdminConfig(AdminConfig):
    default_site = "apps.accounts.admin_site.SecureAdminSite"
