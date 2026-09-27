from typing import ClassVar

from django.contrib import admin

from apps.accounts.models import ApiToken, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "affiliation", "is_admin", "is_active", "created_at")
    list_filter = ("is_admin", "is_active", "is_staff")
    search_fields = ("email", "name", "affiliation")
    readonly_fields = ("id", "created_at", "last_login", "password")
    ordering: ClassVar[list[str]] = ["email"]


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("label", "user", "is_seed", "created_at", "last_used_at", "revoked_at")
    list_filter = ("is_seed",)
    readonly_fields = ("id", "token_hash", "created_at", "last_used_at")
