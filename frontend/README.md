# SGAMS React frontend

The React/Vite app provides the student dashboard, course catalogue and detail,
group list and detail, assignment list and detail, My Tasks/Group Tasks, and
task detail views. The administrator portal provides system summaries, user
and role management, course management, activity review, and report links.
Course, group, assignment, task, and task-update data comes from the Django REST
Framework API. Existing Django workspaces remain available for group
communication, submissions, task progress updates, evidence uploads,
notifications, and profile management.

## Run locally

Start Django on `http://localhost:8000`, then run:

```sh
npm install
npm run dev
```

Vite proxies `/api` requests to Django so the browser uses the same origin for
session-authenticated API requests. Django exposes `/api/csrf/` to establish a
CSRF cookie before the frontend checks `/api/session/`. The shared Axios client
sends session credentials and uses Django's `csrftoken` cookie and
`X-CSRFToken` header for unsafe requests; Django's CSRF middleware remains
enabled. Use the `http://localhost:5173` frontend address with the default
Django `http://localhost:8000` address; using
`127.0.0.1` for one and `localhost` for the other prevents the browser from
sending the same host-scoped session cookie. Set `VITE_DJANGO_BASE_URL` when
Django is running at a different origin; this value also configures the Vite
API proxy. Set `VITE_API_BASE_URL` only when the API is served at a different
path/origin. Production deployments should proxy the API under the frontend
origin or configure Django's trusted origins and credentialed CORS explicitly.

## Authentication and role navigation

Django remains the authentication and authorization authority. The React app
uses Django session authentication through the `/api/session/` identity
endpoint; Django serializers and permission-scoped querysets remain authoritative
for data access. Students see the student portal, administrators see the
administrator portal, and lecturers continue to their existing Django
workspace. The administrator portal uses the authenticated APIs to create
accounts, update roles and active status, and create/update courses. Passwords
are write-only, Django password validation is applied, and administrators
cannot disable or demote themselves through the API.

Administrator-only `/api/admin/summary/` and `/api/admin/activity/` endpoints
provide live system counts and recent audit/activity entries. Existing reports,
assignment management, submissions, grading, task updates, group actions, and
file uploads remain in their Django workspaces; the React portal links to those
workflows rather than duplicating them or suggesting unsupported API behavior.
No database schema changes were required.
