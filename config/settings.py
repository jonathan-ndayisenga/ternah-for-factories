"""
Ternah for Factories — settings.
Dev (no env vars set): SQLite, DEBUG on, matches the old defaults exactly.
Production: set DJANGO_DEBUG=0 and the DB_*/SECRET_KEY/ALLOWED_HOSTS vars below
(see .env.example) — DATABASES switches to PostgreSQL automatically.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me" if DEBUG else "")
if not DEBUG and not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG=0")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*" if DEBUG else "").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Ternah for Factories
    "platformadmin",   # software owner: tenants, subscriptions, tokens, billing lifecycle
    "accounts",        # users, roles, branch attachment, view switcher
    "core",            # Branch + shared bases
    "production",      # raw materials, formulas, batches, QA, distribution
    "sales",           # inventories, POS, debtors, orders, requests
    "finance",         # treasury, disbursements, double-entry ledger, payables
    "reports",         # owner dashboards + chart data
    "manager",         # manager's own home: dashboard, debtors, approvals, inventory — views-only, like reports
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "platformadmin.middleware.SoftLockMiddleware",   # billing soft-lock (SaaS ref §07)
    "accounts.middleware.ActiveViewMiddleware",      # manager view switcher
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "accounts.context_processors.active_view",
        "accounts.context_processors.notifications",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"

DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite" if DEBUG else "postgres")
if DB_ENGINE == "postgres":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        # a 1GB droplet can't afford many idle backends — keep connections short-lived
        # and cap what Postgres itself will ever see at once (also set in postgresql.conf).
        "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Kampala"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"          # collectstatic target; nginx/whitenoise serve from here
if not DEBUG:
    # Manifest storage needs `collectstatic` to have run first (it won't have
    # in local dev unless you remember to), so keep it prod-only.
    STORAGES = {
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    # Assumes TLS is terminated by nginx (see deploy/nginx.conf) and forwarded
    # via X-Forwarded-Proto — required for Django to know the original request was HTTPS.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))  # raise once HTTPS is confirmed working
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
    SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0
    X_FRAME_OPTIONS = "DENY"

    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"console": {"class": "logging.StreamHandler"}},
        "root": {"handlers": ["console"], "level": "INFO"},
        "loggers": {"django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
    }
