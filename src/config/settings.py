"""Settings for Raptor Desk.

Everything is configured through environment variables with safe defaults,
so `docker compose up` works on a fresh clone without an .env file.
"""

import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent  # src/
REPO_DIR = BASE_DIR.parent


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# "demo" seeds fixture data, demo accounts and the checker's fixed tokens.
# "production" refuses to create any of them.
PROFILE = env("RD_PROFILE", "demo")
if PROFILE not in {"demo", "production"}:
    raise ImproperlyConfigured("RD_PROFILE must be 'demo' or 'production'")

DEBUG = env_bool("RD_DEBUG", False)

DATA_DIR = Path(env("RD_DATA_DIR", str(REPO_DIR / "data" / "runtime")))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_secret_key() -> str:
    """Use RD_SECRET_KEY, or create one on first boot and keep it in the data volume.

    O_EXCL makes creation atomic, so the app and worker containers agree on one key.
    """
    configured = os.environ.get("RD_SECRET_KEY")
    if configured:
        return configured
    path = DATA_DIR / "secret_key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(50)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(key)
    return key


SECRET_KEY = _load_secret_key()

ALLOWED_HOSTS = env_list(
    "RD_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],app,host.docker.internal,.app.github.dev"
)
CSRF_TRUSTED_ORIGINS = env_list("RD_CSRF_TRUSTED_ORIGINS", "https://*.app.github.dev")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "core",
    "apps.accounts",
]

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/login"

# Public address of this portal, used in emailed links.
BASE_URL = env("RD_BASE_URL", "http://localhost:8080").rstrip("/")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.principal.PrincipalMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.security.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context.principal",
            ],
        },
    },
]

# SQLite in WAL mode. IMMEDIATE transactions take the write lock up front,
# which avoids "database is locked" errors when reads upgrade to writes.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {
            "init_command": (
                "PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;PRAGMA busy_timeout=5000;"
            ),
            "transaction_mode": "IMMEDIATE",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Sessions and security headers.
SESSION_COOKIE_NAME = "session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("RD_SECURE_COOKIES", False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Outgoing email goes to the local Mailpit inbox in Docker (http://localhost:8025).
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("RD_EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("RD_EMAIL_PORT", "1025"))
EMAIL_USE_TLS = env_bool("RD_EMAIL_USE_TLS", False)
EMAIL_HOST_USER = env("RD_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = env("RD_EMAIL_PASSWORD", "")
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("RD_EMAIL_FROM", "Raptor Desk <desk@raptor-desk.local>")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("RD_LOG_LEVEL", "INFO")},
}
