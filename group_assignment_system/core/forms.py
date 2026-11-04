from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import (
    Assignment,
    Course,
    Group,
    GroupMembership,
    GroupMessage,
    PeerReview,
    Submission,
    Task,
    TaskUpdate,
    User,
)


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = (
            'code',
            'name',
            'programme',
            'year_of_study',
            'semester',
            'lecturer',
            'is_active',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['year_of_study'].required = True
        self.fields['semester'].required = True
        self.fields['year_of_study'].widget.attrs['class'] = 'form-select'
        self.fields['semester'].widget.attrs['class'] = 'form-select'
        lecturers = User.objects.filter(
            role=User.ROLE_LECTURER,
            is_active=True,
        )
        if self.instance.pk and self.instance.lecturer_id:
            lecturers = (
                lecturers
                | User.objects.filter(
                    pk=self.instance.lecturer_id,
                    role=User.ROLE_LECTURER,
                )
            ).distinct()
        self.fields['lecturer'].queryset = lecturers
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-control')

class AssignmentForm(forms.ModelForm):
    peer_evaluation_criteria = forms.MultipleChoiceField(
        choices=Assignment.PEER_EVALUATION_CRITERIA,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Select the criteria students will use when evaluating group members.',
    )

    class Meta:
        model = Assignment
        fields = (
            'course',
            'title',
            'description',
            'objectives',
            'moodle_reference',
            'published_at',
            'deadline',
            'status',
            'min_group_size',
            'max_group_size',
            'allow_student_group_proposals',
            'allow_late_submissions',
            'peer_evaluation_enabled',
            'peer_evaluation_criteria',
        )
        widgets = {
            'published_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'deadline': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'description': forms.Textarea(attrs={'rows': 6}),
            'objectives': forms.Textarea(attrs={'rows': 4}),
            'moodle_reference': forms.URLInput(attrs={'placeholder': 'Optional Moodle activity URL'}),
        }

    def __init__(self, *args, lecturer=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['status'].initial = Assignment.STATUS_DRAFT
            self.fields['peer_evaluation_criteria'].initial = [
                criterion for criterion, _label in Assignment.PEER_EVALUATION_CRITERIA
            ]
        self.fields['published_at'].required = False
        self.fields['objectives'].required = True
        if self.instance.pk:
            self.fields['peer_evaluation_criteria'].initial = (
                self.instance.peer_evaluation_criteria
            )
        course_access = Q(is_active=True)
        if lecturer and not lecturer.is_admin_role() and not lecturer.is_superuser:
            course_access &= (
                Q(lecturer=lecturer) | Q(lecturer_links__lecturer=lecturer)
            )
        if self.instance.pk:
            course_access |= Q(pk=self.instance.course_id)
        self.fields['course'].queryset = Course.objects.filter(
            course_access
        ).distinct()

    def clean(self):
        cleaned_data = super().clean()
        deadline = cleaned_data.get('deadline')
        published_at = cleaned_data.get('published_at')
        status = cleaned_data.get('status')
        now = timezone.now()
        if deadline and status != Assignment.STATUS_CLOSED and deadline <= now:
            self.add_error('deadline', 'The submission deadline must be in the future.')
        if status == Assignment.STATUS_PUBLISHED and not published_at:
            self.add_error('published_at', 'Set a publication date before publishing.')
        if deadline and published_at and published_at >= deadline:
            self.add_error('published_at', 'Publication must be before the submission deadline.')
        if (
            status == Assignment.STATUS_PUBLISHED
            and published_at
            and deadline
            and deadline <= now
        ):
            self.add_error('deadline', 'A published assignment must have a future deadline.')
        if (
            cleaned_data.get('peer_evaluation_enabled')
            and not cleaned_data.get('peer_evaluation_criteria')
        ):
            self.add_error(
                'peer_evaluation_criteria',
                'Select at least one evaluation criterion when peer evaluation is enabled.',
            )
        return cleaned_data

    def save(self, commit=True):
        assignment = super().save(commit=False)
        assignment.peer_evaluation_criteria = self.cleaned_data.get(
            'peer_evaluation_criteria',
            [],
        )
        if commit:
            assignment.save()
            self.save_m2m()
        return assignment


class GroupForm(forms.ModelForm):
    students = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=forms.MultipleHiddenInput,
        label='Students to allocate',
    )

    class Meta:
        model = Group
        fields = ('assignment', 'name', 'description', 'max_members')

    def __init__(self, *args, lecturer=None, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor or lecturer
        assignments = Assignment.objects.select_related('course').filter(
            assignment_type=Assignment.TYPE_GROUP,
            status__in=(Assignment.STATUS_DRAFT, Assignment.STATUS_PUBLISHED),
            course__is_active=True,
        )
        if self.actor and (
            self.actor.is_admin_role() or self.actor.is_superuser
        ):
            pass
        elif self.actor and self.actor.is_lecturer():
            assignments = assignments.filter(
                Q(course__lecturer=self.actor)
                | Q(course__lecturer_links__lecturer=self.actor)
            )
        elif self.actor and self.actor.is_student():
            assignments = assignments.filter(
                representative_appointments__student=self.actor,
                representative_appointments__status='active',
                course__enrolments__student=self.actor,
            ).distinct()
        else:
            assignments = assignments.none()
        self.fields['assignment'].queryset = assignments
        assignment_id = (
            self.data.get('assignment')
            if self.is_bound
            else getattr(self.initial.get('assignment'), 'pk', self.initial.get('assignment'))
        )
        assignment = assignments.filter(pk=assignment_id).first()
        if assignment:
            allocated_students = GroupMembership.objects.filter(
                assignment=assignment,
            ).exclude(
                status=GroupMembership.STATUS_REJECTED,
            ).values('student_id')
            self.fields['students'].queryset = User.objects.filter(
                role=User.ROLE_STUDENT,
                is_active=True,
                course_enrolments__course=assignment.course,
            ).exclude(
                pk__in=allocated_students,
            ).order_by('last_name', 'first_name', 'username').distinct()
        self.fields['max_members'].required = False
        self.fields['max_members'].help_text = (
            'Leave blank to use the assignment maximum.'
        )
        self.fields['assignment'].widget.attrs.setdefault('class', 'form-select')
        self.fields['name'].widget.attrs.setdefault('class', 'form-control')
        self.fields['description'].widget.attrs.setdefault('class', 'form-control')
        self.fields['max_members'].widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        cleaned_data = super().clean()
        assignment = cleaned_data.get('assignment')
        max_members = cleaned_data.get('max_members')
        students = cleaned_data.get('students', [])
        if assignment and max_members is not None and not (
            assignment.min_group_size <= max_members <= assignment.max_group_size
        ):
            self.add_error(
                'max_members',
                f'Choose a capacity from {assignment.min_group_size} to {assignment.max_group_size}.',
            )
        if assignment:
            capacity = (
                assignment.max_group_size
                if max_members is None
                else max_members
            )
            if len(students) > capacity:
                self.add_error(
                    'students',
                    'The selected students exceed the maximum group capacity.',
                )
        if assignment and students:
            existing = GroupMembership.objects.filter(
                assignment=assignment,
                student__in=students,
            ).exclude(status=GroupMembership.STATUS_REJECTED)
            if existing.exists():
                self.add_error(
                    'students',
                    'A selected student already has a group or request for this assignment.',
                )
        return cleaned_data


class GroupUpdateForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ('name', 'description', 'max_members', 'leader')

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group or self.instance
        if self.group and self.group.pk:
            self.fields['leader'].queryset = User.objects.filter(
                group_memberships__group=self.group,
                group_memberships__status__in=(
                    GroupMembership.STATUS_APPROVED,
                    GroupMembership.STATUS_PENDING,
                ),
                role=User.ROLE_STUDENT,
            ).distinct()
        else:
            self.fields['leader'].queryset = User.objects.none()
        self.fields['leader'].required = False

    def clean_max_members(self):
        max_members = self.cleaned_data['max_members']
        assignment = self.group.assignment
        if max_members is None:
            return assignment.max_group_size
        if not assignment.min_group_size <= max_members <= assignment.max_group_size:
            raise ValidationError(
                f'Choose a capacity from {assignment.min_group_size} to {assignment.max_group_size}.'
            )
        if max_members < self.group.reserved_member_count():
            raise ValidationError('Capacity cannot be lower than the current members and invitations.')
        return max_members


class GroupProposalForm(forms.Form):
    name = forms.CharField(max_length=100)
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    invitees = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 5}),
        help_text='Optional: invite course classmates. The lecturer must approve membership.',
    )

    def __init__(self, *args, assignment=None, proposer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.assignment = assignment
        self.proposer = proposer
        if assignment:
            self.fields['invitees'].queryset = User.objects.filter(
                role=User.ROLE_STUDENT,
                is_active=True,
                course_enrolments__course=assignment.course,
            ).exclude(
                Q(group_memberships__assignment=assignment)
                & ~Q(group_memberships__status=GroupMembership.STATUS_REJECTED)
            ).distinct().order_by('last_name', 'first_name', 'username')

    def clean(self):
        cleaned_data = super().clean()
        invitees = cleaned_data.get('invitees', [])
        if self.proposer and self.proposer in invitees:
            self.add_error('invitees', 'You are already the group proposer.')
        if self.assignment and len(invitees) + 1 > self.assignment.max_group_size:
            self.add_error(
                'invitees',
                f'The assignment allows at most {self.assignment.max_group_size} students per group.',
            )
        return cleaned_data


class GroupInvitationForm(forms.Form):
    students = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=True,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 5}),
    )

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        if group:
            self.fields['students'].queryset = User.objects.filter(
                role=User.ROLE_STUDENT,
                is_active=True,
                course_enrolments__course=group.assignment.course,
            ).exclude(
                Q(group_memberships__assignment=group.assignment)
                & ~Q(group_memberships__status=GroupMembership.STATUS_REJECTED)
            ).distinct().order_by('last_name', 'first_name', 'username')

    def clean_students(self):
        students = self.cleaned_data['students']
        if self.group and self.group.reserved_member_count() + students.count() > self.group.capacity:
            raise ValidationError('Invitations would exceed this group’s capacity.')
        return students


class MembershipForm(forms.ModelForm):
    class Meta:
        model = GroupMembership
        fields = ('student',)

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['student'].queryset = User.objects.filter(role=User.ROLE_STUDENT, is_active=True)
        self.group = group
        if group:
            self.fields['student'].queryset = self.fields['student'].queryset.filter(
                course_enrolments__course=group.assignment.course
            ).exclude(
                Q(group_memberships__assignment=group.assignment)
                & ~Q(group_memberships__status=GroupMembership.STATUS_REJECTED)
            ).distinct()

    def clean_student(self):
        student = self.cleaned_data['student']
        if self.group:
            if self.group.reserved_member_count() >= self.group.capacity:
                raise ValidationError('This group has reached its maximum size.')
        return student

    def save(self, commit=True):
        membership = super().save(commit=False)
        membership.group = self.group
        membership.assignment = self.group.assignment
        membership.status = (
            GroupMembership.STATUS_PENDING
            if self.group.status == Group.STATUS_PENDING
            else GroupMembership.STATUS_APPROVED
        )
        if commit:
            existing = GroupMembership.objects.filter(
                assignment=self.group.assignment,
                student=membership.student,
            ).exclude(status=GroupMembership.STATUS_REJECTED).first()
            if existing and existing.pk != membership.pk:
                raise ValidationError('This student already has a group request for the assignment.')
            membership.save()
        return membership


class TaskForm(forms.ModelForm):
    deadline = forms.DateTimeField(
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'],
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M',
            attrs={'type': 'datetime-local'},
        ),
    )

    class Meta:
        model = Task
        fields = ('title', 'description', 'assigned_to', 'priority')

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        self.fields['priority'].required = False
        self.fields['assigned_to'].label = 'Assign to group member'
        self.fields['assigned_to'].help_text = (
            'Choose the student responsible for completing this task.'
        )
        if self.instance.pk and self.instance.deadline:
            self.fields['deadline'].initial = timezone.localtime(self.instance.deadline)
        if group:
            self.fields['assigned_to'].queryset = User.objects.filter(
                group_memberships__group=group,
                group_memberships__status=GroupMembership.STATUS_APPROVED,
                role=User.ROLE_STUDENT,
                is_active=True,
            ).distinct()

    def clean_assigned_to(self):
        user = self.cleaned_data['assigned_to']
        if self.group and not GroupMembership.objects.filter(
            group=self.group,
            student=user,
            status=GroupMembership.STATUS_APPROVED,
        ).exists():
            raise ValidationError('The assignee must be a member of this group.')
        return user

    def clean_priority(self):
        return self.cleaned_data.get('priority') or Task.PRIORITY_NORMAL

    def save(self, commit=True):
        task = super().save(commit=False)
        task.deadline = self.cleaned_data['deadline']
        if commit:
            task.save()
            self.save_m2m()
        return task


class TaskUpdateForm(forms.ModelForm):
    class Meta:
        model = TaskUpdate
        fields = ('update_text',)


class TaskStatusForm(forms.Form):
    status = forms.ChoiceField(choices=Task.STATUS_CHOICES)


class EvidenceUploadForm(forms.Form):
    evidence_file = forms.FileField()

    def clean_evidence_file(self):
        uploaded_file = self.cleaned_data['evidence_file']
        if uploaded_file.size > 10 * 1024 * 1024:
            raise ValidationError('Evidence files must be 10 MB or smaller.')
        allowed = {
            'application/pdf',
            'text/plain',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/zip',
            'image/png',
            'image/jpeg',
        }
        content_type = getattr(uploaded_file, 'content_type', '') or ''
        if content_type and content_type not in allowed and not uploaded_file.name.lower().endswith(
            ('.pdf', '.txt', '.doc', '.docx', '.zip', '.png', '.jpg', '.jpeg')
        ):
            raise ValidationError('This file type is not permitted.')
        return uploaded_file


class GroupDocumentUploadForm(forms.Form):
    MAX_FILE_SIZE = 25 * 1024 * 1024
    ALLOWED_CONTENT_TYPES = {
        '.pdf': {'application/pdf'},
        '.doc': {'application/msword'},
        '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
        '.xls': {'application/vnd.ms-excel', 'application/x-excel'},
        '.xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
        '.ppt': {'application/vnd.ms-powerpoint'},
        '.pptx': {'application/vnd.openxmlformats-officedocument.presentationml.presentation'},
        '.txt': {'text/plain'},
        '.zip': {'application/zip', 'application/x-zip-compressed'},
    }
    document = forms.FileField()

    def clean_document(self):
        uploaded_file = self.cleaned_data['document']
        filename = uploaded_file.name.replace('\\', '/').rsplit('/', 1)[-1]
        extension = f".{filename.rsplit('.', 1)[-1].lower()}" if '.' in filename else ''
        if extension not in self.ALLOWED_CONTENT_TYPES:
            raise ValidationError(
                'Upload a PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT, or ZIP file.'
            )
        if uploaded_file.size > self.MAX_FILE_SIZE:
            raise ValidationError('Group documents must be 25 MB or smaller.')
        content_type = (getattr(uploaded_file, 'content_type', '') or '').lower()
        if content_type and content_type not in (
            self.ALLOWED_CONTENT_TYPES[extension] | {'application/octet-stream'}
        ):
            raise ValidationError('The file content type does not match its extension.')
        return uploaded_file


class PeerReviewForm(forms.ModelForm):
    class Meta:
        model = PeerReview
        fields = ('evaluated', 'comment')
        widgets = {'comment': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, assignment=None, evaluator=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.assignment = assignment
        self.evaluator = evaluator
        self.criterion_fields = {}
        if assignment:
            labels = dict(Assignment.PEER_EVALUATION_CRITERIA)
            for criterion in assignment.peer_evaluation_criteria:
                field_name = f'criterion_{criterion}'
                self.fields[field_name] = forms.IntegerField(
                    min_value=1,
                    max_value=10,
                    label=labels.get(criterion, criterion.replace('_', ' ').title()),
                    help_text='Score from 1 (low) to 10 (high).',
                )
                self.criterion_fields[criterion] = field_name
        group_ids = (
            assignment.groups.filter(
                status__in=(Group.STATUS_ACTIVE, Group.STATUS_CLOSED),
            ).values_list('id', flat=True)
            if assignment
            else []
        )
        self.fields['evaluated'].queryset = User.objects.filter(
            group_memberships__group_id__in=group_ids,
            group_memberships__status=GroupMembership.STATUS_APPROVED,
            is_active=True,
        ).exclude(pk=evaluator.pk if evaluator else None).distinct()
        # Peer reviews are limited to other approved members of the evaluator's groups.

    def clean(self):
        cleaned_data = super().clean()
        evaluated = cleaned_data.get('evaluated')
        if self.assignment and not self.assignment.peer_evaluation_enabled:
            raise ValidationError('Peer evaluation is not enabled for this assignment.')
        if self.assignment and self.evaluator and evaluated:
            same_group = GroupMembership.objects.filter(
                group__assignment=self.assignment,
                student=self.evaluator,
                status=GroupMembership.STATUS_APPROVED,
            ).filter(
                group__memberships__student=evaluated,
                group__memberships__status=GroupMembership.STATUS_APPROVED,
            ).exists()
            if not same_group:
                raise ValidationError('You can only evaluate an approved member of your group.')
            if PeerReview.objects.filter(
                assignment=self.assignment,
                evaluator=self.evaluator,
                evaluated=evaluated,
            ).exists():
                raise ValidationError('You have already reviewed this student for this assignment.')
            if evaluated.pk == self.evaluator.pk:
                raise ValidationError('You cannot evaluate yourself.')
        return cleaned_data

    def save(self, commit=True):
        review = super().save(commit=False)
        scores = {
            criterion: self.cleaned_data[field_name]
            for criterion, field_name in self.criterion_fields.items()
        }
        if scores:
            review.criteria_scores = scores
            review.score = round(sum(scores.values()) / len(scores))
        if commit:
            review.save()
            self.save_m2m()
        return review


class GroupMessageForm(forms.ModelForm):
    class Meta:
        model = GroupMessage
        fields = ('task', 'body')
        widgets = {'body': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Write a group message...'})}

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        self.fields['task'].required = False
        self.fields['task'].empty_label = 'General discussion'
        if group:
            self.fields['task'].queryset = group.tasks.order_by('title', 'pk')

    def clean_task(self):
        task = self.cleaned_data.get('task')
        if task and self.group and task.group_id != self.group.pk:
            raise ValidationError('Choose a task belonging to this group.')
        return task


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ('file', 'notes', 'moodle_reference')
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
            'moodle_reference': forms.URLInput(
                attrs={'placeholder': 'Optional Moodle submission/receipt URL'}
            ),
        }

    def clean_file(self):
        uploaded_file = self.cleaned_data['file']
        if uploaded_file.size > 20 * 1024 * 1024:
            raise ValidationError('Submission files must be 20 MB or smaller.')
        upload_validation = GroupDocumentUploadForm(files={'document': uploaded_file})
        if not upload_validation.is_valid():
            raise ValidationError(upload_validation.errors['document'][0])
        return uploaded_file


class GradeForm(forms.Form):
    score = forms.DecimalField(max_digits=5, decimal_places=2, min_value=0)
    max_score = forms.DecimalField(max_digits=5, decimal_places=2, min_value=1, initial=100)
    feedback = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}), required=False)
    release_now = forms.BooleanField(required=False, initial=False)

    def clean(self):
        cleaned = super().clean()
        score = cleaned.get('score')
        max_score = cleaned.get('max_score')
        if score is not None and max_score is not None and score > max_score:
            raise ValidationError('Score cannot exceed the maximum score.')
        return cleaned
