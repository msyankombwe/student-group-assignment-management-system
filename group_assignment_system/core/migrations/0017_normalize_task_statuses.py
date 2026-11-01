from django.db import migrations, models


def normalize_pending_tasks(apps, schema_editor):
    Task = apps.get_model('core', 'Task')
    Task.objects.using(schema_editor.connection.alias).filter(status='Pending').update(
        status='todo',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_assignment_peer_evaluation_criteria_and_more'),
    ]

    operations = [
        migrations.RunPython(normalize_pending_tasks, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='task',
            name='status',
            field=models.CharField(
                choices=[
                    ('todo', 'Not Started'),
                    ('in_progress', 'In Progress'),
                    ('under_review', 'Under Review'),
                    ('completed', 'Completed'),
                ],
                default='todo',
                max_length=20,
            ),
        ),
    ]
