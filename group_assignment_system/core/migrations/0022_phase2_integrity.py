from django.db import migrations


def refuse_reverse_with_individual_submissions(apps, schema_editor):
    Submission = apps.get_model('core', 'Submission')
    database = schema_editor.connection.alias
    if Submission.objects.using(database).filter(group__isnull=True).exists():
        raise RuntimeError(
            'Cannot reverse Phase 2 while individual submissions exist. '
            'Export or otherwise preserve those submissions before downgrading.'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_assignmentclassrepresentative_appointed_by_full_name_snapshot_and_more'),
    ]

    operations = [
        migrations.RunPython(
            migrations.RunPython.noop,
            refuse_reverse_with_individual_submissions,
        ),
    ]
