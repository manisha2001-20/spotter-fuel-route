"""Django settings for the Spotter fuel-route API.

Every deployment-specific value is read from environment variables (optionally
from a local `.env` file; see `.env.example`). Nothing secret is committed.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


DEBUG = env_bool("DJANGO_DEBUG", True)

# A random key must be supplied whenever DEBUG is off.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false")
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

# Turn on when served over HTTPS (off by default so local runs work over HTTP).
if env_bool("DJANGO_SECURE_SSL", False):
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "fuel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", BASE_DIR / "db.sqlite3"),
    }
}

# Geocoding and routing responses are cached on disk so that repeat requests
# make no external calls, and the cache survives server restarts.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.environ.get("DJANGO_CACHE_DIR", BASE_DIR / ".cache"),
        "TIMEOUT": 60 * 60 * 24 * 30,  # 30 days
        "OPTIONS": {"MAX_ENTRIES": 5000},
    }
}

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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "fuel.exceptions.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}

# --- Fuel-route domain settings -------------------------------------------
FUEL_ROUTE = {
    # Assessment assumptions
    "VEHICLE_RANGE_MILES": env_float("VEHICLE_RANGE_MILES", 500.0),
    "MILES_PER_GALLON": env_float("MILES_PER_GALLON", 10.0),
    # A station counts as "on the route" if its location is within this many
    # miles of the route line. Stations are located at their city centre (the
    # CSV has no coordinates), so the corridor has to allow for that offset.
    "CORRIDOR_MILES": env_float("CORRIDOR_MILES", 10.0),
    # With an empty starting tank, stations in the first N route miles may be
    # used for the first fill-up (see README "Assumptions").
    "START_WINDOW_MILES": env_float("START_WINDOW_MILES", 25.0),
    # Skip a station when an equal-or-cheaper one is within this many route
    # miles of it (0 = consider every station: strict mathematical optimum).
    "DOMINANCE_WINDOW_MILES": env_float("DOMINANCE_WINDOW_MILES", 15.0),
    # External services (both free, no API key required)
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    "NOMINATIM_BASE_URL": os.environ.get("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org"),
    "HTTP_USER_AGENT": os.environ.get("HTTP_USER_AGENT", "spotter-fuel-route/1.0"),
    "GEOCODE_TIMEOUT_SECONDS": env_float("GEOCODE_TIMEOUT_SECONDS", 8.0),
    "ROUTE_TIMEOUT_SECONDS": env_float("ROUTE_TIMEOUT_SECONDS", 20.0),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"fuel": {"handlers": ["console"], "level": os.environ.get("FUEL_LOG_LEVEL", "INFO")}},
}
