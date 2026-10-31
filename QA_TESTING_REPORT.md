# UNILUS SGAMS Quality & Security Test Report

**Test date:** 31 October 2026
**Scope:** Django application, database-backed workflows, role authorization, report exports, and rendered UI smoke checks.
**Test method:** Automated Django `TestCase` suite plus Django system/deployment checks.
**Database:** Django test database for automated tests; existing local SQLite database for browser smoke checks. No external service or live Moodle integration was tested.

## Test results

| Test case ID | Test description | Expected result | Actual result | Status |
|---|---|---|---|---|
| AUTH-01 | Valid student login followed by logout | Valid credentials open the dashboard; logout ends the session and protected pages redirect to login. | `test_login_and_post_logout_protect_authenticated_pages` passed: dashboard opened; GET logout was rejected; POST logout redirected; dashboard then required login. | PASS |
| AUTH-02 | Invalid login attempt | Invalid credentials do not authenticate; user receives a generic error. | `test_invalid_login_is_rejected_and_does_not_create_authenticated_session` passed: login returned the generic error and dashboard redirected to login. | PASS |
| AUTH-03 | Password-reset flow | Reset email/link is sent, single-use, and new passwords pass configured validators. | `test_password_reset_flow_sends_single_use_link_and_enforces_validators` passed. | PASS |
| AUTH-04 | CSRF protection for login and mutations | POST requests without CSRF tokens are rejected and create no mutation. | `test_login_and_mutation_forms_enforce_csrf_tokens` passed: both requests returned 403; no message was created. | PASS |
| RBAC-01 | Student, lecturer, and administrator dashboard access | Each role sees its own dashboard; student cannot access administrator functions. | `test_dashboard_and_navigation_are_specific_to_each_role` passed for all three roles. | PASS |
| RBAC-02 | Student access to another group | Private group data and files are denied; scoped lists omit unrelated groups/tasks. | `test_nonmember_cannot_view_group`, `test_invited_student_cannot_view_group_private_content`, `test_group_and_task_navigation_pages_are_role_scoped`, and file/download scope tests passed. | PASS |
| RBAC-03 | Lecturer cross-course access | Lecturer operations and reports only expose managed courses. | `test_lecturer_cannot_edit_assignment_for_another_lecturers_course`, `test_lecturer_submission_list_is_scoped_to_managed_courses`, and report-scope tests passed. | PASS |
| RBAC-04 | Administrator functions | Administrator role can manage authorized records and access administrative pages. | `test_administrator_role_can_manage_django_admin_without_superuser_status` and administrator course/session tests passed. | PASS |
| COURSE-01 | Course creation and academic calendar management | Administrator can create/manage courses, lecturer links, semesters, academic years, and roster; non-admins are denied. | `test_administrator_can_create_course_and_assign_a_lecturer`, `test_administrator_manages_academic_years_semesters_courses_and_roster`, `test_lecturers_cannot_manage_courses`, and `test_students_cannot_manage_courses` passed. | PASS |
| ASSIGN-01 | Assignment creation, publication, editing, and closure | Authorized lecturer can manage assignments for managed active courses; required values, dates, and group sizes are validated. | `test_lecturer_creates_publishes_edits_and_closes_assignment`, `test_lecturer_assignment_creation_is_limited_to_assigned_active_courses`, and `test_assignment_forms_reject_invalid_publication_deadlines_and_group_sizes` passed. | PASS |
| GROUP-01 | Group proposal, approval, rejection, and invitations | Student proposals and join requests await lecturer action; lecturer can approve/reject; invitations behave as configured. | Proposal/invitation, join request, approval, rejection, and re-application workflow tests passed. | PASS |
| GROUP-02 | Duplicate membership and capacity | Duplicate assignment membership and capacity violations are rejected. | `test_duplicate_group_memberships_are_rejected` and `test_group_capacity_and_assignment_membership_are_enforced` passed. | PASS |
| TASK-01 | Task allocation and reassignment | Task assignee must be an approved group member; task updates preserve assignment relationships. | `test_task_assignee_must_be_an_approved_group_member`, `test_task_reassignment_updates_the_task_assignment_relation`, and group task workflow tests passed. | PASS |
| TASK-02 | Internal deadlines and overdue status | Valid deadlines are persisted; overdue is derived from the deadline and completion status. | Deadline/task tests, including `test_overdue_status_is_derived_and_invalid_status_is_rejected`, passed. | PASS |
| FILE-01 | Supported file formats and versioning | Supported academic formats upload, download privately, and retain version/uploader metadata. | `test_group_workspace_uploads_versions_and_protects_document_lifecycle` passed for PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT, and ZIP. | PASS |
| FILE-02 | Rejected uploads and private downloads | Unsupported/oversized uploads are rejected; unauthorised downloads/deletions are denied. | `test_group_document_upload_rejects_unsupported_and_oversized_files`, `test_evidence_upload_and_download_are_limited_to_group_access`, and `test_submission_download_is_limited_to_group_and_course_access` passed. | PASS |
| COMM-01 | Group messages and discussion visibility | Authorized member messages are recorded; unrelated users cannot view group discussions. | `test_group_discussions_are_private_and_added_to_activity_history` passed. | PASS |
| SEC-01 | Stored XSS in message content | User-supplied markup is rendered as text, not executable HTML. | `test_group_discussion_content_is_html_escaped` passed: script markup was HTML-escaped. | PASS |
| CONTRIB-01 | Contribution activity provenance | Activities retain actor/student/group/task attribution and cannot be silently edited. | `test_contribution_events_preserve_actor_and_student_attribution_and_are_immutable` passed. | PASS |
| NOTIFY-01 | Notification generation, access, and deduplication | Events/reminders notify appropriate users, links are scoped, users can only mark their own notifications read, and repeated reminders do not duplicate. | Notification access/history, scheduled assignment publication, and deadline reminder/deduplication tests passed. | PASS |
| SUBMIT-01 | Submission timestamp, resubmission, and status | Submission evidence is timestamped; one latest record is maintained; invalid late submissions do not replace valid records. | Submission/resubmission and late-submission rejection tests passed. | PASS |
| FEEDBACK-01 | Lecturer feedback confidentiality and publication | Unreleased assessment is lecturer-only; released feedback is visible to the group and triggers notification. | `test_submission_receipt_hides_unpublished_feedback_then_shows_released_feedback` and feedback notification tests passed. | PASS |
| PEER-01 | Peer evaluation validation and confidentiality | Self/duplicate/disabled evaluations are rejected; authorized lecturer can view scoped summaries. | Peer evaluation validation, criteria, setting, and confidential-report tests passed. | PASS |
| REPORT-01 | Analytics accuracy, filters, and role scope | Report types render with scoped data; unauthorized cross-course/group filters are rejected; CSV columns/data reflect filtered results. | Report accuracy, role scope, filter, and CSV export tests passed, including injection-like group filter input (403). | PASS |
| REPORT-02 | Spreadsheet formula injection in contribution export | User-controlled text is exported as literal text, not a spreadsheet formula. | Security review identified the issue; after remediation, `test_contribution_csv_escapes_spreadsheet_formula_cells` passed for a leading-space formula payload. | PASS |
| UI-01 | Responsive/navigation smoke check | Role navigation resolves to useful pages; narrow viewport has no horizontal page overflow. | Browser smoke check opened the documents/discussions workspace at the browser's effective 300 CSS-pixel viewport; page width matched viewport and scoped group links rendered. | PASS |

## Execution summary

- Full Django test suite after the corrective change: **67 tests passed, 0 failed**.
- `python manage.py check`: **passed**, no issues.
- `python manage.py makemigrations --check --dry-run`: **passed**, no pending model migrations.
- `git diff --check`: **passed**.
- Production-style `check --deploy` with `DEBUG=False`, a non-default secret, hosts, HTTPS redirect, secure cookies, and HSTS enabled: **no critical deployment warnings**. Django reports optional `SECURE_HSTS_INCLUDE_SUBDOMAINS` and `SECURE_HSTS_PRELOAD` recommendations. These remain unset unless UNILUS confirms every subdomain is HTTPS-only and preload eligibility is intended.
- `check --deploy` with the local development defaults reports development-only warnings (DEBUG, default development secret, and insecure local cookies/redirect); do not deploy using those defaults.

## Security review finding and corrective action

| Severity | Finding | Status | Corrective action |
|---|---|---|---|
| Medium | Contribution CSV export could write a group name beginning with a spreadsheet formula prefix as an executable spreadsheet cell. | Fixed and regression-tested | Added shared spreadsheet-cell escaping for formula-prefixed and tab/carriage-return-prefixed text in report and contribution CSV exports. |

## Security checks and limitations

- ORM querysets and parameterized Django filters are used for application queries; an injection-like report filter is rejected with HTTP 403. No SQL injection was demonstrated.
- CSRF middleware is enabled and missing tokens were tested against login and group-message POSTs.
- Discussion content escaping was directly regression-tested. This does not constitute a formal template-wide static XSS proof.
- File upload extension/size validation and access-checked downloads were tested. The application stores uploads locally; production deployment must keep the media directory private and serve downloads only through access-checked views.
- Session security flags are enabled in production configuration (`DEBUG=False`); the development configuration intentionally allows local HTTP.
- This was an application-level test run, not a penetration test or independent infrastructure assessment. Moodle upload/grade synchronization is outside scope and is not claimed.
