from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import LoginForm, PreferencesForm


class AccountLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm


class AccountLogoutView(LogoutView):
    next_page = "accounts:login"
    http_method_names = ["post", "options"]


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def account(request):
    """Separate forms never accept permissions; password updates keep this session."""

    preferences_form = PreferencesForm(instance=request.user)
    password_form = PasswordChangeForm(request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "preferences":
            preferences_form = PreferencesForm(request.POST, instance=request.user)
            if preferences_form.is_valid():
                preferences_form.save()
                messages.success(request, "Vos préférences ont été enregistrées.")
                return redirect("accounts:account")
        elif action == "password":
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Votre mot de passe a été modifié.")
                return redirect("accounts:account")
        else:
            return render(request, "accounts/account.html", {
                "preferences_form": preferences_form,
                "password_form": password_form,
                "action_error": "Cette action n’est pas disponible.",
            }, status=400)
    return render(request, "accounts/account.html", {
        "preferences_form": preferences_form,
        "password_form": password_form,
    })
