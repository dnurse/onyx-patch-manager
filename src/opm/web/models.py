"""Web security and audit records, stored in the pre-existing OPM SQLite file."""
from django.conf import settings
from django.db import models


class LoginFailure(models.Model):
    """Database-backed login throttling survives restarts and worker recycling.

    Hash the attempted username and source address rather than persisting raw
    identifiers. This is a modest abuse control, not a substitute for keeping
    this high-privilege interface behind a private network and HTTPS.
    """
    identifier_hash = models.CharField(max_length=64, db_index=True)
    created = models.DateTimeField(auto_now_add=True, db_index=True)


class AuditEvent(models.Model):
    """Immutable application-level record of the authenticated actor.

    Never put passwords, tokens, SSH private keys or resolved Compose config
    into these records. Errors are handled separately by the controller.
    """
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=64)
    subject = models.CharField(max_length=128, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created',)
