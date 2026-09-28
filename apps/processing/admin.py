from django.contrib import admin, messages
from django.utils import timezone

from .models import ProcessingJob
from .services import retry_job


@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = ("media", "kind", "status", "attempts", "created_at", "error_message")
    list_filter = ("status", "kind")
    list_select_related = ("media",)
    readonly_fields = tuple(field.name for field in ProcessingJob._meta.fields)
    actions = ("retry_selected",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Relancer les traitements en erreur ou désactivés")
    def retry_selected(self, request, queryset):
        count, cursor = 0, None
        snapshot = queryset.filter(created_at__lte=timezone.now())
        while True:
            batch = snapshot.order_by("pk")
            if cursor is not None:
                batch = batch.filter(pk__gt=cursor)
            jobs = list(batch.select_related("media")[:100])
            if not jobs:
                break
            for job in jobs:
                count += retry_job(job) is not None
            cursor = jobs[-1].pk
        self.message_user(request, f"{count} traitement(s) ajouté(s) à la file.", messages.SUCCESS)
