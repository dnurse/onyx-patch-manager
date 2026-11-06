"""WSGI entry point used by Gunicorn under the unprivileged `opm` account."""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'opm.web.settings')
from django.core.wsgi import get_wsgi_application  # noqa: E402
application = get_wsgi_application()
