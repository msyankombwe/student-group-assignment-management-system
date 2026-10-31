from django.db import migrations, models


def migrate_legacy_records(apps, schema_editor):
    database = schema_editor.connection.alias
    User = apps.get_model('core', 'User')
    StudentProfile = apps.get_model('core', 'StudentProfile')
    LecturerProfile = apps.get_model('core', 'LecturerProfile')
    AdministratorProfile = apps.get_model('core', 'AdministratorProfile')
    Course = apps.get_model('core', 'Course')
    AcademicSession = apps.get_model('core', 'AcademicSession')
    Semester = apps.get_model('core', 'Semester')
    Task = apps.get_model('core', 'Task')
    TaskAssignment = apps.get_model('core', 'TaskAssignment')
    InternalDeadline = apps.get_model('core', 'InternalDeadline')
    Group = apps.get_model('core', 'Group')
    GroupFile = apps.get_model('core', 'GroupFile')
    ContributionEvidence = apps.get_model('core', 'ContributionEvidence')
    FileDocument = apps.get_model('core', 'FileDocument')
    Submission = apps.get_model('core', 'Submission')
    Grade = apps.get_model('core', 'Grade')
    ActivityLog = apps.get_model('core', 'ActivityLog')
    ContributionActivity = apps.get_model('core', 'ContributionActivity')

    users = User.objects.using(database)
    for user in users.iterator():
        if user.role == 'student':
            StudentProfile.objects.using(database).update_or_create(
                user_id=user.pk,
                defaults={
                    'student_number': user.student_number or None,
                    'programme': user.programme,
                    'phone': user.phone,
                },
            )
        elif user.role == 'lecturer':
            LecturerProfile.objects.using(database).update_or_create(
                user_id=user.pk,
                defaults={'phone': user.phone},
            )
        if user.role == 'admin' or user.is_superuser:
            AdministratorProfile.objects.using(database).update_or_create(
                user_id=user.pk,
                defaults={'phone': user.phone},
            )

    for course in Course.objects.using(database).all():
        session = None
        if course.academic_session_id:
            session = AcademicSession.objects.using(database).get(pk=course.academic_session_id)
        elif course.academic_year:
            session, _ = AcademicSession.objects.using(database).get_or_create(
                name=course.academic_year,
            )
        if session and course.semester:
            semester, _ = Semester.objects.using(database).get_or_create(
                name=course.semester,
                academic_session_id=session.pk,
            )
            Course.objects.using(database).filter(pk=course.pk).update(
                academic_session_id=session.pk,
                semester_record_id=semester.pk,
            )

    for task in Task.objects.using(database).iterator():
        InternalDeadline.objects.using(database).update_or_create(
            task_id=task.pk,
            defaults={
                'due_at': task.deadline,
                'created_by_id': task.created_by_id,
            },
        )
        InternalDeadline.objects.using(database).filter(task_id=task.pk).update(
            created_at=task.created_at,
        )
        TaskAssignment.objects.using(database).get_or_create(
            task_id=task.pk,
            student_id=task.assigned_to_id,
            defaults={'assigned_by_id': task.created_by_id},
        )
        TaskAssignment.objects.using(database).filter(
            task_id=task.pk,
            student_id=task.assigned_to_id,
        ).update(assigned_at=task.created_at)

    for document in GroupFile.objects.using(database).iterator():
        group = Group.objects.using(database).get(pk=document.group_id)
        copied = FileDocument.objects.using(database).create(
            group_id=group.pk,
            assignment_id=group.assignment_id,
            uploaded_by_id=document.uploaded_by_id,
            file=document.file.name,
            original_filename=document.original_filename,
            file_type=document.file_type,
            file_size=document.file_size,
            version=document.version,
            uploaded_at=document.uploaded_at,
            document_type='group_file',
        )
        FileDocument.objects.using(database).filter(pk=copied.pk).update(
            uploaded_at=document.uploaded_at,
        )

    for evidence in ContributionEvidence.objects.using(database).iterator():
        task = Task.objects.using(database).get(pk=evidence.task_id)
        copied = FileDocument.objects.using(database).create(
            group_id=task.group_id,
            assignment_id=Group.objects.using(database).get(pk=task.group_id).assignment_id,
            task_id=task.pk,
            uploaded_by_id=evidence.student_id,
            file=evidence.evidence_file.name,
            original_filename=evidence.original_filename,
            file_size=evidence.file_size,
            uploaded_at=evidence.timestamp,
            document_type='contribution_evidence',
        )
        FileDocument.objects.using(database).filter(pk=copied.pk).update(
            uploaded_at=evidence.timestamp,
        )

    for submission in Submission.objects.using(database).all():
        group = Group.objects.using(database).get(pk=submission.group_id)
        Submission.objects.using(database).filter(pk=submission.pk).update(
            assignment_id=group.assignment_id,
        )

    for grade in Grade.objects.using(database).all():
        submission = Submission.objects.using(database).get(pk=grade.submission_id)
        Grade.objects.using(database).filter(pk=grade.pk).update(
            group_id=submission.group_id,
            assignment_id=submission.assignment_id,
        )

    latest_by_group = {}
    latest_submissions = list(
        Submission.objects.using(database)
        .filter(is_latest=True)
        .order_by('submitted_at', 'pk')
    )
    for submission in latest_submissions:
        latest_by_group[submission.group_id] = submission.pk
    for submission in latest_submissions:
        if latest_by_group[submission.group_id] != submission.pk:
            Submission.objects.using(database).filter(pk=submission.pk).update(is_latest=False)

    student_ids = users.filter(role='student').values_list('pk', flat=True)
    logs = ActivityLog.objects.using(database).filter(user_id__in=student_ids)
    for log in logs.iterator():
        copied = ContributionActivity.objects.using(database).create(
            student_id=log.user_id,
            assignment_id=log.assignment_id,
            group_id=log.group_id,
            task_id=log.task_id,
            event_type=log.event_type,
            description=log.description,
        )
        ContributionActivity.objects.using(database).filter(pk=copied.pk).update(
            occurred_at=log.timestamp,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_administratorprofile_contributionactivity_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_records),
        migrations.AddConstraint(
            model_name='submission',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_latest', True)),
                fields=('group',),
                name='one_latest_submission_per_group',
            ),
        ),
        migrations.RemoveField(model_name='contributionevidence', name='student'),
        migrations.RemoveField(model_name='contributionevidence', name='task'),
        migrations.RemoveField(model_name='groupfile', name='assignment'),
        migrations.RemoveField(model_name='groupfile', name='group'),
        migrations.RemoveField(model_name='groupfile', name='uploaded_by'),
        migrations.RemoveField(model_name='grade', name='assignment'),
        migrations.RemoveField(model_name='grade', name='group'),
        migrations.RemoveField(model_name='submission', name='assignment'),
        migrations.RemoveField(model_name='task', name='deadline'),
        migrations.RemoveField(model_name='user', name='phone'),
        migrations.RemoveField(model_name='user', name='programme'),
        migrations.RemoveField(model_name='user', name='student_number'),
        migrations.DeleteModel(name='ContributionEvidence'),
        migrations.DeleteModel(name='GroupFile'),
    ]
