# UNILUS SGAMS — Final Demonstration & Technical Guide

**System status:** Django prototype for academic demonstration. It complements Moodle; it is not a replacement and does not synchronize Moodle submissions or official grades.

## 1. System overview

The University of Lusaka Student Group Assignment Management System (SGAMS) supports course and academic-calendar administration, assignment publishing, student group formation, task planning, shared documents and discussions, activity evidence, in-app notifications, submission records, lecturer feedback, peer evaluation, and role-scoped reports.

The primary interface is a server-rendered Django application. The repository also contains a Vite/React scaffold; it is not the main user interface and should not be started as the SGAMS demonstration application.

## 2. System architecture

```text
Browser
  └── Django templates + Bootstrap 5 + application CSS
       ├── Django views and form validation
       ├── Role and object-scope checks
       ├── Django ORM and migrations
       │    └── SQLite (development) / PostgreSQL (configured deployment option)
       ├── Private local media (downloaded through authorization-checked views)
       └── Optional Django REST Framework API (/api/)
```

- Django views render the primary UI and validate workflow actions.
- Django forms and model validation handle input and business rules; role and object permissions are enforced server-side.
- ORM queries and relational constraints persist users, academic records, groups, tasks, files, submissions, feedback, and activity.
- Bootstrap, application CSS, and Chart.js (CDN) provide responsive presentation and data-backed reporting visuals.
- The optional API uses session authentication and scoped permission/queryset logic. The primary demonstration workflow uses HTML pages.
- Deadline reminders use a management command; Django does not run a background scheduler itself.

## 3. Technology stack

| Area | Technology |
|---|---|
| Language | Python 3 |
| Web framework | Django 5.2 |
| Primary interface | Django templates, Bootstrap 5, custom CSS |
| Report chart | Chart.js 4 (CDN) |
| Optional API | Django REST Framework |
| Database | SQLite locally; PostgreSQL configuration supported |
| File storage | Django local media storage in development |
| Automated tests | Django `TestCase` runner |
| Optional scaffold | Vite/React, not the primary UI |

Installed Python packages are declared in the repository-root `requirements.txt`. PostgreSQL deployments also need a compatible PostgreSQL server/driver in the deployment environment.

## 4. Database architecture

Django models and migrations are the schema source of truth. The relational model includes:

- Users with one-to-one student, lecturer, and administrator profiles.
- Courses, lecturer assignments, student enrolments, academic sessions, and semesters.
- Assignments, groups, assignment-scoped group memberships, tasks, task assignments, and internal deadlines.
- Versioned group files, task evidence, group messages, submissions, and lecturer grades/feedback.
- Peer reviews, contribution activities, notifications, and system activity/audit records.

Important integrity and access rules include one group per student per assignment, group capacity validation, membership-scoped task assignment, one latest submission per group, feedback publication control, and authorization-checked private file downloads. See [DATABASE_ERD.md](./DATABASE_ERD.md) for the ERD, relationship details, constraints, and migration history.

## 5. Main modules

| Module | Demonstrable capability |
|---|---|
| Authentication & profiles | Login/logout, password reset flow, profile pages, role-specific navigation and dashboards |
| Academic administration | Administrator course, lecturer, semester, and academic-year management; Django admin for account administration |
| Courses & assignments | Course listing/details; lecturer assignment create, publish, edit, close, and instructions |
| Groups & membership | Lecturer-created groups, student proposals, invitations, join requests, approvals/rejections, member management |
| Assignment representatives | Assignment-specific appointment/replacement/revocation, enrolled-student eligibility, representative group creation and membership changes, immutable action history |
| Tasks & deadlines | Group task creation and assignment, priority, status/progress updates, internal deadlines, overdue display |
| Collaboration workspace | Private group documents with version metadata, discussions, members, task activity, activity history |
| Contributions | Recorded in-app activity summaries and lecturer reports; evidence only, not a grade or effort score |
| Notifications | In-app notifications, unread counts/history, read actions, event links, reminder management command |
| Submission & feedback | Group file/evidence, submission receipt/status/history, lecturer assessment and controlled feedback publication |
| Peer evaluation | Assignment-configurable criteria and confidential lecturer summaries |
| Reports & analytics | Role-scoped reports and CSV exports; group progress visualizations |

## 6. Roles and permissions

- **Student:** sees enrolled-course assignments and their own group memberships. Collaboration content is limited to approved groups; students can perform permitted group and task activities, submit work, view released feedback, and evaluate peers when enabled.
- **Assignment representative:** an active student enrolled in the assignment's course, appointed for one group assignment by its managing lecturer or an administrator. The representative can create groups and make permitted membership changes only for that assignment. The representative cannot edit assignment details, approve/reject group requests, dissolve groups, grade, or manage feedback. Appointment does not grant lecturer privileges or access to other assignments in the same course.
- **Lecturer/supervisor:** manages assigned courses and their assignments/groups; can appoint, replace, and revoke assignment representatives; review and correct group/membership records; approve/reject proposals; dissolve groups; and review representative activity, submissions, contribution evidence, grades, and feedback for managed courses.
- **Administrator:** manages accounts through the Django administration site, academic calendar and courses, and views administrative reports.

Server-side scope checks apply independently of what navigation displays. Group endpoints, membership services, and audit queries verify assignment/course scope. The administrator role and Django `is_superuser` flag are distinct; a demonstration administrator has both for access to Django admin.

## 7. Installation and database setup

Run PowerShell commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

For local development, the example configuration uses SQLite and `DEBUG=True`. Keep the development secret and settings local; never use the demo configuration or credentials in a deployed environment.

Apply database migrations and populate the fictional demonstration dataset:

```powershell
Set-Location .\group_assignment_system
python manage.py migrate
python manage.py seed_demo_data
```

`seed_demo_data` is repeatable and refreshes the named demo accounts and demonstration records. In particular, it resets the passwords for those named accounts to the values below. Use only in a local demonstration database; do not run against production or an institutional database.

Optional: create a separate administrator interactively:

```powershell
python manage.py createsuperuser
```

## 8. Demonstration accounts

All names and records generated by `seed_demo_data` are fictional.

| Username | Password | Role |
|---|---|---|
| `admin.demo` | `DemoAdmin123!` | Administrator and Django superuser |
| `lecturer.banda` | `DemoLecturer123!` | Lecturer for BIT326 |
| `lecturer.phiri` | `DemoLecturer123!` | Lecturer for BIT340 |
| `student01` through `student12` | `DemoStudent123!` | Students, enrolled in demo courses |

These are intentionally predictable demo-only credentials. Change/remove the accounts and passwords before any shared or public deployment.

### Seeded records

- **Courses:** BIT326 Software Engineering and BIT340 Database Systems, academic year 2026, Semester 1.
- **Assignments:** Collaborative Project Management Prototype (group proposal and peer review enabled) and Relational Schema Case Study.
- **Groups:** Alpha and Beta for BIT326; Gamma and Delta for BIT340, each with three seeded members.
- **Tasks:** Alpha has a late/in-progress requirements task, an upcoming wireframes task, and a completed demo-script task. Beta has an in-progress schema-review task.
- **Documents:** Alpha includes a clearly labelled fictional requirements-outline text document.
- **Contributions/discussions:** Alpha has sample tasks, messages, contribution activity, and activity records attributed to fictional demo users.
- **Submissions/feedback:** Alpha has a sample submission and released demo feedback; Beta has no submission, showing the outstanding-submission case.
- **Other records:** A sample peer review and linked in-app notifications.

The seed content is for presentation and test data only. It must not be represented as real student work or official assessment.

## 9. Start the application

From `group_assignment_system/`:

```powershell
python manage.py runserver 127.0.0.1:8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) and sign in using a demo account. Stop the development server with `Ctrl+C`.

Optional pre-demo checks:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

The application test suite has been run against the test database. `check --deploy` must be run with deployment-specific environment settings; local development warnings are expected when using the example DEBUG/HTTP configuration.

## 10. End-to-end live demonstration scenario

This sequence creates fresh workflow records while retaining the seeded examples. It demonstrates real application actions, not a scripted or simulated integration.

1. **Lecturer creates the assignment.** Sign in as `lecturer.banda`, open Assignments → Create assignment, choose BIT326, provide title/instructions/objectives, set a future deadline and group size (for example 3), allow student proposals, and publish.
2. **Student proposes a group.** Sign out and sign in as `student01`. Open the new published BIT326 assignment and submit a group proposal, inviting `student02` and `student03`. The group and invitations remain pending until the lecturer acts.
3. **Lecturer reviews and approves.** Sign in as `lecturer.banda`, open the group proposal from Groups or the dashboard, inspect the proposal, then approve it. Invitees can accept; the lecturer can approve their membership requests.
4. **Members plan work.** Sign in as an approved student, open the group workspace, create tasks, assign each to an approved member, set priority/deadline, and record a progress update or status change.
5. **Collaborate.** Upload a supported file (for example a small TXT/PDF), review uploader/version metadata, and post a group discussion message. Use separate student accounts to show the private shared workspace.
6. **Monitor contribution and progress.** Sign in as the lecturer and view the group workspace, progress report, student contribution report, recent activity, and task deadlines. Explain that activity is supporting evidence, not an automatic measure of effort or academic merit.
7. **Record submission and feedback.** As an approved group student, upload a submission through the group workspace. As the lecturer, review the record and save feedback; demonstrate that unpublished feedback is not visible to students, then publish it and verify access on the submission receipt.
8. **Show notifications and reports.** Review an in-app notification as the student, then use an authorized lecturer report filter and CSV export.

### Assignment representative scenario

1. Sign in as `lecturer.banda` and open a group assignment in a course the lecturer manages.
2. In **Assignment Representative**, select an active, enrolled student. Confirm the chooser labels include student ID and full name, then appoint the representative.
3. Open the assignment as that student. Confirm group creation is available for this assignment, but instructions remain private while the assignment is a draft and the edit/grade controls are absent.
4. Create a group and allocate only active course-enrolled students. Attempting an already allocated or unenrolled student is rejected. Open a different assignment, including one in the same course, and confirm representative controls are denied there.
5. As the lecturer, review the group and membership history. Correct a group name, add/remove/transfer a member as appropriate, and verify the corresponding audit events identify the lecturer and preserve the prior status/group.
6. As the lecturer, appoint a different representative or revoke the current appointment with a reason. Verify the appointment status/history and representative event are recorded, then confirm the former representative can no longer create groups or manage memberships for that assignment.
7. Attempt assignment editing and grading as the representative; these requests must be denied. Complete the grading/feedback demonstration as the lecturer.

Audit history is append-only through the application. New events store actor, course, assignment, student, group, status, and representative snapshots where those values are available. Historical rows are retained; missing snapshots are shown as **Not recorded** and are not inferred from current users or courses.

For the fastest “ready-made data” walkthrough, use seeded Alpha to show documents, messages, task progress, contribution evidence, a submission, and published feedback. To demonstrate proposal/approval from a clean starting state, create a **new** BIT326 assignment: the seeded students already have groups for the preloaded assignment, but group membership uniqueness is scoped to each assignment.

## 11. Final quality-check record

Checks actually run for this demonstration preparation:

- All migrations were applied on the local development database, and `seed_demo_data` was run successfully to populate it.
- The `seed_demo_data` command is covered by an automated test for repeatable record counts, seeded documents/contributions/notifications, and successful demo-account login plus role page access.
- The full Django `core` suite passed on 11 November 2026: **128 tests passed, 0 failed**.
- The focused audit/representative suite passed: **18 tests passed, 0 failed**.
- Django `check` and migration consistency checks passed at the latest full code check.
- `py_compile` passed for the changed Django forms, views, and audit/representative tests. `makemigrations --check --dry-run` reported no model changes without migrations.
- The existing SQLite database passed a read-only integrity check and reported 24 applied core migrations. The automated test runner used an isolated test database; no database reset or data-changing UAT was performed.
- Role workflows, file access/upload validation, notifications, submission/feedback, peer review, reports, CSRF, invalid login, and discussion HTML escaping have automated coverage.
- Assignment representative appointment, replacement, revocation, course-enrolment checks, assignment scoping, lecturer oversight, audit filters, CSV exports, and historical snapshot rendering have dedicated automated coverage.
- A local runserver started successfully and served the login page with HTTP 200; automated tests also logged in as each role and loaded its dashboard and representative pages. Browser smoke checks previously confirmed role navigation and responsive pages render, with no horizontal page overflow at the tested narrow viewport.
- The latest browser check refreshed the login page without authenticated writes to the existing database. A separate clean-machine installation, a real PostgreSQL deployment, formal stakeholder UAT, assistive-technology testing, and independent penetration testing were not performed.

## 12. Feature status, limitations, and future work

### Implemented and demonstrable

- Django account login/logout, password reset flow, role-scoped pages and data, administrator Django admin access.
- Course/calendar and assignment management.
- Lecturer-created groups and student-proposed groups/invitations/approval flows.
- Assignment-level representative appointment/replacement/revocation, enrolled-student eligibility, assignment-scoped group and membership actions, lecturer oversight, and immutable audit history.
- Group members, task allocation, statuses, internal deadlines and overdue calculation.
- Private group documents, supported extensions/size limits, uploader/version metadata, access-checked downloads and deletion.
- Group discussion/messages and activity records.
- Contribution summaries and reports, notifications, submission history, lecturer feedback publication, and peer evaluation.
- Role-scoped dashboards/reports and CSV exports.

### Partially implemented / prototype limitations

- Moodle references are optional metadata only: SGAMS does not upload to Moodle, verify receipt there, sync rosters, or synchronize official grades.
- Deadline reminders are implemented as a management command. Delivery depends on an external host scheduler; no scheduler is bundled/running inside Django.
- Notification delivery is in-app; SMS/push/email event delivery is not implemented. Password-reset email can use SMTP configuration; development uses the console backend.
- Reports export CSV only. Native PDF/XLSX exports are not included. Charts are limited to data-backed report views rather than a full analytics suite.
- Contributions count recorded system events only; offline work/quality cannot be inferred. No automated grade or peer-evaluation decision is made.
- Upload checks validate supported file formats and size; antivirus/content scanning and object storage are not configured. Local media storage is for development/demo and must remain private.
- The React/Vite folder is a scaffold, not an integrated SPA frontend.
- Administrative “system settings” use the Django admin interface; a separate branded settings console is not implemented.

### Not implemented / future improvements

- Approved Moodle API integration and institutional identity/roster synchronization.
- Production deployment, operational monitoring, backup/restore drills, and formal load/security penetration testing.
- Managed production file storage, malware scanning, retention/consent controls, and a documented institutional data-governance policy.
- Background-job service, delivery channels, retry/monitoring, and per-course reminder configuration.
- Native PDF/XLSX report exports and broader trend analytics.
- Formal user-acceptance testing with UNILUS stakeholders and assistive-technology testing across supported browsers/devices.

## 13. Deployment safety

Before any shared deployment: set a strong private `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, production hosts/trusted origins, HTTPS, secure cookies, a production database, SMTP, private media storage, backups, and authorized administrator accounts. Never deploy with demo credentials or public access to the media directory. Use [README.md](./README.md) and `.env.example` for environment settings.
