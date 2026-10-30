"""Base settings shared by every environment. Values come from the environment (.env in dev)."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-secret-key-please-change-me-0123456789")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    # project
    "apps.core",
    "apps.accounts",
    "apps.stores",
    "apps.catalog",
    "apps.orders",
    "apps.payments",
    "apps.reports",
    "apps.simulator",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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

# --- Database -------------------------------------------------------------
# DATABASE_URL=mysql://fastpos:fastpos@127.0.0.1:3306/fastpos in prod/CI.
# Falls back to SQLite so tests and the simulator run on a machine without MySQL credentials.
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "isolation_level": "read committed",
        }
    )
    DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Africa/Mogadishu")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- REST framework -------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "apps.stores.authentication.KioskTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "FastPOS API",
    "DESCRIPTION": "Self-checkout kiosk backend. Kiosk endpoints use `Authorization: Kiosk <token>`; "
    "admin endpoints use `Authorization: Bearer <jwt>`.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "ENUM_NAME_OVERRIDES": {
        "OrderStatusEnum": "apps.orders.models.Order.Status",
        "PaymentStatusEnum": "apps.payments.models.Payment.Status",
        "PaymentMethodEnum": "apps.payments.models.Payment.Method",
        "WeightResultEnum": "apps.orders.models.WeightVerification.Result",
        "WeightOutcomeEnum": "apps.orders.models.WeightVerification.Outcome",
        "WeightStatusEnum": "apps.orders.models.OrderItem.WeightStatus",
        "UserRoleEnum": "apps.accounts.models.User.Role",
    },
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:5173"])

# --- Celery ---------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_TIME_LIMIT = 150
CELERY_TASK_SOFT_TIME_LIMIT = 140
CELERY_BEAT_SCHEDULE = {
    "reconcile-pending-payments": {
        "task": "apps.payments.tasks.reconcile_pending_payments",
        "schedule": 60.0,
    },
    "cancel-stale-orders": {
        "task": "apps.orders.tasks.cancel_stale_orders",
        "schedule": 300.0,
    },
}

# --- Business settings ----------------------------------------------------
STALE_ORDER_MINUTES = env.int("STALE_ORDER_MINUTES", default=30)
PAYMENT_RECONCILE_AFTER_MINUTES = env.int("PAYMENT_RECONCILE_AFTER_MINUTES", default=3)
PAYMENT_RECONCILE_GIVE_UP_MINUTES = env.int("PAYMENT_RECONCILE_GIVE_UP_MINUTES", default=30)

# Payment providers. PAYMENT_PROVIDER_BACKEND="mock" routes every method to the MockProvider.
PAYMENT_PROVIDER_BACKEND = env("PAYMENT_PROVIDER_BACKEND", default="mock")
PAYMENT_MOCK_OUTCOME = env("PAYMENT_MOCK_OUTCOME", default="success")  # success | fail | timeout
PAYMENT_MOCK_DELAY_SECONDS = env.float("PAYMENT_MOCK_DELAY_SECONDS", default=0)
PAYMENT_HTTP_TIMEOUT_SECONDS = env.int("PAYMENT_HTTP_TIMEOUT_SECONDS", default=120)

WAAFI_API_URL = env("WAAFI_API_URL", default="https://api.waafipay.net/asm")
WAAFI_MERCHANT_UID = env("WAAFI_MERCHANT_UID", default="")
WAAFI_API_USER_ID = env("WAAFI_API_USER_ID", default="")
WAAFI_API_KEY = env("WAAFI_API_KEY", default="")

EDAHAB_API_URL = env("EDAHAB_API_URL", default="https://edahab.net/api/api")
EDAHAB_API_KEY = env("EDAHAB_API_KEY", default="")
EDAHAB_AGENT_CODE = env("EDAHAB_AGENT_CODE", default="")
EDAHAB_SECRET = env("EDAHAB_SECRET", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
