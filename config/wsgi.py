"""WSGI entry point. Bootstrap runs before Django reads settings."""
from config.bootstrap import prepare

prepare()

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
