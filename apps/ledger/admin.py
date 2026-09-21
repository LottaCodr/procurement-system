from django.contrib import admin

from ledger.models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    """Read-only, deliberately. There is no editable field here and no delete
    action, because the ledger is append-only: an admin panel that could rewrite
    history would undo the entire integrity claim."""

    list_display = ("seq", "event_type", "aggregate", "actor", "occurred_at", "row_hash_short")
    list_filter = ("event_type",)
    search_fields = ("aggregate", "actor")
    readonly_fields = [f.name for f in Event._meta.fields]
    date_hierarchy = "occurred_at"

    @admin.display(description="hash")
    def row_hash_short(self, obj) -> str:
        return obj.row_hash[:12] + "…"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
