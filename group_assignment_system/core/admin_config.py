from django.contrib.admin.apps import AdminConfig


class UNILUSAdminConfig(AdminConfig):
    default_site = 'core.admin_site.UNILUSAdminSite'
