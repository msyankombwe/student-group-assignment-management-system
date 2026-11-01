# UNILUS Student Group Assignment Management System (SGAMS)

Web application that helps University of Lusaka (UNILUS) manage courses, academic calendars, and **group assignments**: publication, group formation, tasks, files/messages, submissions, grading, activity indicators, and notifications.

See [FINAL_DEMONSTRATION_GUIDE.md](./FINAL_DEMONSTRATION_GUIDE.md) for the full technical overview, database and role descriptions, installation steps, fictional demo account details, walkthrough, verified checks, and known limitations.

This system is a **collaborative management layer that complements the UNILUS LMS**. It does **not** replace the LMS and does **not** claim live university-system integration until that work is authorised and implemented.

## Technology stack

| Layer | Choice |
| --- | --- |
| Backend | Python 3 + Django 5 |
| UI | Django templates + Bootstrap 5 + Chart.js (CDN) |
| API (optional) | Django REST Framework under `/api/` (authenticated) |
| Dev database | SQLite (`group_assignment_system/db.sqlite3`) |
| Production DB | PostgreSQL (via `POSTGRES_*` environment variables) |
| Tests | Django test framework |

> There is also a Vite/React scaffold under `frontend/`. The **primary demonstrable UI is the Django/Bootstrap app**. Keep the React folder for optional future SPA work; do not treat it as a second production application.

## Database design

The Django models and migrations are the source of truth for the relational schema. See [DATABASE_ERD.md](./DATABASE_ERD.md) for the entity relationship diagram, key constraints, and migration notes. Apply schema changes with `python manage.py migrate` from `group_assignment_system/`.

## Quick start

```powershell
cd "C:\Users\morga\Documents\Group assignment management"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
cd group_assignment_system
python manage.py migrate
python manage.py seed_demo_data
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in with a demo account below.

### Demo accounts (fictional only)

| Username | Password | Role |
| --- | --- | --- |
| `admin.demo` | `DemoAdmin123!` | Administrator |
| `lecturer.banda` | `DemoLecturer123!` | Lecturer |
| `lecturer.phiri` | `DemoLecturer123!` | Lecturer |
| `student01` … `student12` | `DemoStudent123!` | Student |

`seed_demo_data` is idempotent (safe to re-run).

## First demonstrable workflow

1. Log in as `admin.demo`, create an academic year and semester under **Academic calendar**, then create a course assigned to `lecturer.banda`.
2. Log in as `lecturer.banda`, create and publish an assignment for that course, then create groups or review student-proposed groups.
3. Add enrolled students or approve join requests and invitations; students can propose groups when the assignment allows it.
4. Create and assign group tasks to approved group members, set priorities and internal deadlines, track creators and last-updated times, and monitor overdue work. Students can distinguish their **My Tasks** from all **Group Tasks**, then update task status or post progress from task details. The server enforces group membership for students and assignment ownership for lecturers across both the web interface and API. Task statuses are Not Started, In Progress, Under Review, and Completed; Overdue is calculated from the deadline and completion status rather than stored.
5. Students in approved groups can use the workspace to share versioned PDF, Office, TXT, and ZIP documents, discuss assignment work, and review activity history.
6. Log in as a group student (`student01`) to view published instructions, deadlines, membership status, post a message, update tasks, and submit work.
7. As lecturer, review group communication and documents, review the submission, release feedback, and monitor contribution records.

## Submission and feedback boundary

SGAMS records group submission files, timestamps, workflow status, optional evidence notes, and optional Moodle course/activity or receipt URLs. A Moodle reference is metadata only: SGAMS does not upload to Moodle, verify a Moodle receipt, or synchronize official grades. Submit through Moodle separately whenever required by the course. Lecturer assessment and feedback stored in SGAMS are not official Moodle grades unless an approved integration is later implemented and tested.

## Running tests

```powershell
cd group_assignment_system
python manage.py test core
```

## Notifications and deadline reminders

Notifications are stored in the database, shown in the navigation bar and paginated notification history, and linked to their related assignment, group, or task. Users can mark individual notifications or all notifications as read. Reminder creation is idempotent for each user, deadline, and reminder window.

The prototype does not run a background scheduler inside Django. Configure the host scheduler (for example, Windows Task Scheduler or cron) to run this command every 15 minutes from `group_assignment_system/`:

```powershell
python manage.py send_deadline_reminders --hours-before 24,1 --window-minutes 60
```

The default sends task and final-assignment reminders within 60 minutes after the 24-hour and 1-hour lead-time marks. It also delivers notifications for scheduled assignment publications once their publication time arrives, and notifies the assigned student and course lecturer(s) about each overdue, incomplete task. If the scheduler is stopped, upcoming reminders can be missed; overdue task notifications and due publication notifications are generated on the next successful run. Adjust the lead times and window for the institution's policy.

## Project layout

```
Group assignment management/
  requirements.txt
  .env.example
  README.md
  REQUIREMENTS_TRACEABILITY.md
  frontend/                 # optional React scaffold (not primary UI)
  group_assignment_system/
    manage.py
    db.sqlite3              # local only; ignored by git
    core/                   # main application
    group_assignment_system/  # Django project settings
```

## Security notes

- Passwords are hashed by Django (never stored in plaintext), and Django's configured password validators apply to reset requests.
- CSRF protection, session rotation on login, POST-only logout, and one-hour browser sessions are enabled.
- Password reset uses Django's single-use reset tokens. In development, reset emails print to the runserver console; configure SMTP for production.
- Student, lecturer, and administrator permissions are enforced server-side for page views, API endpoints, the administration site, and group file downloads.
- Students see their approved groups and only the limited details needed for their own pending proposals/invitations; private rosters, tasks, files, messages, and submissions require approved membership. Group documents are uploaded with randomized storage names and must be served through access-checked download views, never directly from the media directory.
- Workspace document uploads accept PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, TXT, and ZIP files up to 25 MB. Uploads and deletions are recorded in group activity history.
- Put secrets in `.env` (see `.env.example`). Do not commit `.env`, databases, or uploaded media.
- Activity logs are **recorded in-app actions**, not definitive proof of student effort.

## Git / team collaboration

```powershell
git init
git add .
git commit -m "Initial UNILUS SGAMS foundation"
git branch -M main
git remote add origin <your-github-url>
git push -u origin main
```

Recommended workflow: feature branches → pull requests → review → merge. Avoid force-pushing shared branches.

Suggested module ownership for a small team:

- Auth, users, admin
- Courses & assignments
- Groups & membership
- Tasks & files/messages
- Submissions, grading, reports
- Tests & documentation

## Deployment checklist (not yet deployed)

1. Set `DJANGO_DEBUG=False`, strong `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS`.
2. Configure PostgreSQL via `POSTGRES_*` variables.
3. Run `python manage.py migrate`.
4. Collect static files: `python manage.py collectstatic`.
5. Serve uploaded files only through the application's access-checked download views; do not expose the media directory as public static content. Enable HTTPS and secure cookies.
6. Create a real administrator account; disable demo passwords.
7. Smoke-test login, assignment create, group access denial, submission and grading.

## Academic fairness notice

Contribution/activity reports show actions performed inside this application. They do **not** perfectly measure work quality or effort completed offline.

## Reports and analytics

The role-scoped reports area includes assignment submissions, group progress, student contribution, inactive groups, student tasks, group performance summaries, administrative activity, and course assignment summaries. Reports can be filtered by authorised course, assignment, group, semester, and academic year. CSV exports are provided and can be opened in spreadsheet software; native XLSX and PDF generation are not included in this prototype.
