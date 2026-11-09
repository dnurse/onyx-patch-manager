"""Preserve audit actor attribution even after an administrator deletes the login."""
from django.db import migrations, models


def backfill_actor_names(apps, schema_editor):
    AuditEvent = apps.get_model('opm_web', 'AuditEvent')
    for entry in AuditEvent.objects.select_related('actor').filter(actor__isnull=False).iterator():
        AuditEvent.objects.filter(pk=entry.pk).update(actor_name=entry.actor.username)


class Migration(migrations.Migration):
    dependencies = [('opm_web', '0001_initial')]
    operations = [
        migrations.AddField(model_name='auditevent', name='actor_name',
                            field=models.CharField(max_length=150, blank=True, default='')),
        migrations.RunPython(backfill_actor_names, migrations.RunPython.noop),
    ]
