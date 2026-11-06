"""Create only the new web tables, leaving the controller's tables untouched."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='LoginFailure', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('identifier_hash', models.CharField(max_length=64, db_index=True)),
            ('created', models.DateTimeField(auto_now_add=True, db_index=True)),
        ]),
        migrations.CreateModel(name='AuditEvent', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('action', models.CharField(max_length=64)),
            ('subject', models.CharField(max_length=128, blank=True)),
            ('created', models.DateTimeField(auto_now_add=True)),
            ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
        ], options={'ordering': ('-created',)}),
    ]
