from django.apps import AppConfig


class ProcurementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "procurement"
    label = "procurement"
    verbose_name = "Procurement"

    def ready(self):
        from procurement import signals  # noqa: F401
