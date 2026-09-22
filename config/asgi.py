"""ASGI entry point. Vercel serves this module (ASGI wins when both are set).

Bootstrap runs before Django reads settings. A blank ``DJANGO_SETTINGS_MODULE``
in the project environment must not survive into the function: every request
would 500 with the same ImproperlyConfigured the build used to die on.
"""
from config.bootstrap import prepare

prepare()

from django.core.asgi import get_asgi_application  # noqa: E402

application = get_asgi_application()
