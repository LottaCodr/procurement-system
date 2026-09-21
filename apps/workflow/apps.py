from django.apps import AppConfig


class WorkflowConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workflow"
    verbose_name = "Procurement workflow (Phases 2-5)"

    def ready(self):
        from workflow import signals  # noqa: F401
