from django.contrib import admin

from apps.events.models import Criterion, CustomQuestion, Event, PhaseTransition, Prize, Track


class TrackInline(admin.TabularInline):
    model = Track
    extra = 0


class CriterionInline(admin.TabularInline):
    model = Criterion
    extra = 0


class PrizeInline(admin.TabularInline):
    model = Prize
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "phase", "submissions_close_at", "created_at")
    list_filter = ("phase",)
    search_fields = ("name", "slug")
    inlines = (TrackInline, CriterionInline, PrizeInline)


@admin.register(CustomQuestion)
class CustomQuestionAdmin(admin.ModelAdmin):
    list_display = ("label", "event", "kind", "required")


@admin.register(PhaseTransition)
class PhaseTransitionAdmin(admin.ModelAdmin):
    list_display = ("event", "from_phase", "to_phase", "overridden", "at")
    readonly_fields = ("event", "from_phase", "to_phase", "actor_id", "reason", "preflight", "at")
