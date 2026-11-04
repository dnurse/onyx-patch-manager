from django.apps import AppConfig


class OPMWebConfig(AppConfig):
    """Keep OPM-specific authentication auditing in its own Django app."""
    name = 'opm.web'
    label = 'opm_web'
    verbose_name = 'Onyx Patch Manager'
