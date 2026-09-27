import contextlib

from django import forms
from django.contrib.auth import login, logout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts import services
from core.http import ApiError
from core.policy import Rule, define, policy

define("public.auth", Rule(public=True, description="Sign-in pages are public."))


class PasswordLoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)


class MagicLinkForm(forms.Form):
    email = forms.EmailField()


def _safe_next(request: HttpRequest) -> str:
    target = request.POST.get("next") or request.GET.get("next") or "/"
    allowed = url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return target if allowed else "/"


@require_http_methods(["GET", "POST"])
@policy("public.auth")
def login_view(request: HttpRequest) -> HttpResponse:
    form = PasswordLoginForm(request.POST or None)
    context = {"form": form, "next": _safe_next(request), "error": None}
    if request.method == "POST" and form.is_valid():
        try:
            user = services.password_login(
                request, form.cleaned_data["email"], form.cleaned_data["password"]
            )
        except ApiError as exc:
            retry = exc.details.get("retry_after", 60)
            context["error"] = f"Too many sign-in attempts. Try again in {retry} seconds."
            return render(request, "accounts/login.html", context, status=429)
        if user is not None:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return HttpResponseRedirect(context["next"])
        context["error"] = "Email or password is incorrect."
    return render(request, "accounts/login.html", context)


@require_POST
@policy("public.auth")
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return HttpResponseRedirect("/")


@require_http_methods(["GET", "POST"])
@policy("public.auth")
def magic_request_view(request: HttpRequest) -> HttpResponse:
    form = MagicLinkForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        # When rate limited, respond exactly as on success so nothing is revealed.
        with contextlib.suppress(ApiError):
            services.send_magic_link(request, form.cleaned_data["email"])
        return render(request, "accounts/magic_sent.html")
    return render(request, "accounts/magic_request.html", {"form": form})


@require_http_methods(["GET", "POST"])
@policy("public.auth")
def magic_confirm_view(request: HttpRequest, token: str) -> HttpResponse:
    # GET only shows a button: mail scanners that prefetch links must not use up the token.
    if request.method == "GET":
        return render(request, "accounts/magic_confirm.html", {"token": token})
    user = services.consume_magic_link(token)
    if user is None:
        return render(request, "accounts/magic_invalid.html", status=400)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponseRedirect("/")
