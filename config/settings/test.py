from .base import *  # noqa: F401,F403

DEBUG = False
CELERY_TASK_ALWAYS_EAGER = True
PAYMENT_PROVIDER_BACKEND = "mock"
PAYMENT_MOCK_OUTCOME = "success"
PAYMENT_MOCK_DELAY_SECONDS = 0
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests
