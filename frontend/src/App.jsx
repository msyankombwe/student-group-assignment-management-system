import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { BrowserRouter, Link, NavLink, Route, Routes, useParams, useSearchParams } from 'react-router-dom'
import { filterTasks } from './taskFilters.js'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'
const DJANGO_BASE = (import.meta.env.VITE_DJANGO_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')
const EMPTY_API_LIST = []
const YEAR_OF_STUDY_OPTIONS = [1, 2, 3, 4]
const SEMESTER_OPTIONS = [1, 2]
const apiClient = axios.create({
  baseURL: `${API_BASE}/`,
  withCredentials: true,
  withXSRFToken: true,
  xsrfCookieName: 'csrftoken',
  xsrfHeaderName: 'X-CSRFToken',
})

const studentLinks = [
  { label: 'My Groups', to: '/groups' },
  { label: 'My Assignments', to: '/assignments' },
  { label: 'My Tasks', to: '/tasks' },
  { label: 'Contributions', href: '/contributions/' },
  { label: 'Notifications', href: '/notifications/' },
]

const lecturerLinks = [
  { label: 'Managed Courses', href: '/courses/' },
  { label: 'Assignments', href: '/assignments/' },
  { label: 'Groups', href: '/groups/' },
  { label: 'Tasks', href: '/tasks/' },
  { label: 'Submissions', href: '/submissions/' },
  { label: 'Reports', href: '/reports/' },
]

function apiErrorMessage(error) {
  const status = error.response?.status
  if (status === 401 || status === 403) {
    return {
      message: 'Sign in to the Django portal with an account that can access this information.',
      authRequired: true,
    }
  }
  if (error.response) {
    return {
      message: 'The SGAMS API could not load this information. Please try again.',
      authRequired: false,
    }
  }
  if (error.request) {
    return {
      message: 'Could not connect to the SGAMS API. Check that the Django server is running.',
      authRequired: false,
    }
  }
  return {
    message: 'The SGAMS API request failed unexpectedly. Please try again.',
    authRequired: false,
  }
}

function useApiData(endpoint, shape = 'list') {
  const emptyData = shape === 'list' ? EMPTY_API_LIST : null
  const [data, setData] = useState(emptyData)
  const [state, setState] = useState({ loading: true, error: '', authRequired: false })
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    if (!endpoint) return undefined
    const controller = new AbortController()

    const request = endpoint === 'session'
      ? apiClient.get('csrf/', { signal: controller.signal }).then(() => (
          apiClient.get('session/', { signal: controller.signal })
        ))
      : apiClient.get(`${endpoint}/`, { signal: controller.signal })

    request
      .then(({ data: response }) => {
        const result = shape === 'list'
          ? (Array.isArray(response) ? response : response?.results)
          : response
        const valid = shape === 'list'
          ? Array.isArray(result)
          : result && typeof result === 'object' && !Array.isArray(result)
        if (!valid) {
          setData(emptyData)
          setState({
            loading: false,
            error: 'The SGAMS API returned data in an unsupported format.',
            authRequired: false,
          })
          return
        }
        setData(result)
        setState({ loading: false, error: '', authRequired: false })
      })
      .catch((error) => {
        if (axios.isCancel(error)) return
        const { message, authRequired } = apiErrorMessage(error)
        setData(emptyData)
        setState({ loading: false, error: message, authRequired })
      })

    return () => controller.abort()
  }, [emptyData, endpoint, retryCount, shape])

  const retry = () => {
    setState({ loading: true, error: '', authRequired: false })
    setRetryCount((count) => count + 1)
  }

  return { data, ...state, retry }
}

function useApiList(endpoint) {
  return useApiData(endpoint, 'list')
}

function useApiRecord(endpoint) {
  return useApiData(endpoint, 'record')
}

function Brand() {
  return (
    <Link className="brand" to="/" aria-label="SGAMS home">
      <span className="brand-mark" aria-hidden="true">SG</span>
      <span className="brand-copy">
        <strong>SGAMS</strong>
        <span>Group assignment workspace</span>
      </span>
    </Link>
  )
}

function PortalLayout() {
  const [menuOpen, setMenuOpen] = useState(false)
  const closeMenu = () => setMenuOpen(false)
  const session = useApiRecord('session')
  const user = session.data?.role ? session.data : null
  const userName = user ? [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username : ''
  const initials = userName.slice(0, 1).toUpperCase()

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="site-header">
        <div className="header-inner">
          <Brand />
          <button
            aria-controls="primary-navigation"
            aria-expanded={menuOpen}
            aria-label={menuOpen ? 'Close navigation menu' : 'Open navigation menu'}
            className="menu-toggle"
            onClick={() => setMenuOpen((open) => !open)}
            type="button"
          >
            <span aria-hidden="true">{menuOpen ? '×' : '☰'}</span>
          </button>
          <nav
            aria-label="Primary navigation"
            className={`primary-navigation${menuOpen ? ' is-open' : ''}`}
            id="primary-navigation"
          >
            <NavLink end onClick={closeMenu} to="/">Home</NavLink>
            <NavLink onClick={closeMenu} to="/dashboard">Dashboard</NavLink>
            {session.data?.role === 'student' && <NavLink onClick={closeMenu} to="/courses">My Courses</NavLink>}
            {session.data?.role === 'admin' && <>
              <NavLink onClick={closeMenu} to="/users">Users &amp; Roles</NavLink>
              <NavLink onClick={closeMenu} to="/courses">Courses</NavLink>
              <NavLink onClick={closeMenu} to="/activity">System Activity</NavLink>
              <NavLink onClick={closeMenu} to="/reports">Reports</NavLink>
            </>}
            {session.data?.role === 'lecturer' && lecturerLinks.map((item) => (
              <a href={`${DJANGO_BASE}${item.href}`} key={item.label}>{item.label}</a>
            ))}
            {session.data?.role === 'student' && studentLinks.map((item) => (
              item.to
                ? <NavLink key={item.label} onClick={closeMenu} to={item.to}>{item.label}</NavLink>
                : <a href={`${DJANGO_BASE}${item.href}`} key={item.label}>{item.label}</a>
            ))}
          </nav>
          <details className="account-menu">
            <summary aria-label="Open account menu">
              <span className="account-avatar" aria-hidden="true">
                <svg focusable="false" viewBox="0 0 24 24">
                  {initials
                    ? <text dominantBaseline="middle" textAnchor="middle" x="12" y="13">{initials}</text>
                    : <>
                        <circle cx="12" cy="8" r="3.5" />
                        <path d="M4.5 20c.7-3.7 3.2-5.5 7.5-5.5s6.8 1.8 7.5 5.5" />
                      </>}
                </svg>
              </span>
              <span className="account-label">{userName || 'Account'}</span>
              <span className="account-chevron" aria-hidden="true">⌄</span>
            </summary>
            <div className="account-popover">
              {user
                ? <>
                    <span className="account-role">{user.role}</span>
                    <a href={`${DJANGO_BASE}/profile/`}>Profile</a>
                    <a href={`${DJANGO_BASE}/`}>Open Django portal</a>
                  </>
                : <a href={`${DJANGO_BASE}/login/`}>Sign in to SGAMS</a>}
            </div>
          </details>
        </div>
      </header>

      <main className="content-panel" id="main-content">
        {session.loading || session.error
          ? <div className="page-content"><ApiState
              authRequired={session.authRequired}
              error={session.error}
              loading={session.loading}
              retry={session.retry}
            /></div>
          : session.data.role === 'admin'
            ? <Routes>
                <Route element={<AdminDashboardPage />} path="/" />
                <Route element={<AdminDashboardPage />} path="/dashboard" />
                <Route element={<UserPage user={session.data} />} path="/users" />
                <Route element={<CourseManagementPage />} path="/courses" />
                <Route element={<AdminActivityPage />} path="/activity" />
                <Route element={<AdminReportsPage />} path="/reports" />
                <Route element={<DjangoPortalNotice role={session.data.role} />} path="*" />
              </Routes>
            : session.data.role !== 'student'
              ? <DjangoPortalNotice role={session.data.role} />
            : <Routes>
                <Route element={<DashboardPage user={session.data} />} path="/" />
                <Route element={<DashboardPage user={session.data} />} path="/dashboard" />
                <Route element={<CoursePage />} path="/courses" />
                <Route element={<CourseDetailPage />} path="/courses/:id" />
                <Route element={<GroupListPage />} path="/groups" />
                <Route element={<GroupDetailPage />} path="/groups/:id" />
                <Route element={<AssignmentListPage />} path="/assignments" />
                <Route element={<AssignmentDetailPage />} path="/assignments/:id" />
                <Route element={<TaskListPage user={session.data} />} path="/tasks" />
                <Route element={<TaskDetailPage user={session.data} />} path="/tasks/:id" />
                <Route element={<NotFoundPage />} path="*" />
              </Routes>}
      </main>

      <footer className="site-footer">
        <span>SGAMS · Student Group Assignment Management System</span>
        <span>University of Lusaka · Complements the university LMS</span>
      </footer>
    </div>
  )
}

function StatusBadge({ active }) {
  return (
    <span className={`status-badge ${active ? 'status-badge--active' : 'status-badge--muted'}`}>
      <span aria-hidden="true" className="status-dot" />
      {active ? 'Active' : 'Inactive'}
    </span>
  )
}

function CourseCard({ course }) {
  const courseCode = course.code || 'Course'
  const classification = [
    course.year_of_study_label || (course.year_of_study ? `Year ${course.year_of_study}` : ''),
    course.semester_label || (course.semester ? `Semester ${course.semester}` : ''),
  ].filter(Boolean).join(' · ')

  return (
    <article className="portal-card course-card">
      <div className="course-card-top">
        <span className="course-code">{courseCode}</span>
        <StatusBadge active={course.is_active} />
      </div>
      <h3>{course.name}</h3>
      {course.programme && <p className="card-detail">{course.programme}</p>}
      {classification && <p className="card-detail">{classification}</p>}
      <Link className="text-action" to={`/courses/${course.id}`}>
        Open course <span aria-hidden="true">→</span>
      </Link>
    </article>
  )
}

function SummaryCard({ label, value, detail, href, tone = 'blue' }) {
  const content = (
    <>
      <span className="summary-card-label">{label}</span>
      <strong className="summary-card-value">{value}</strong>
      <span className="summary-card-detail">{detail}</span>
    </>
  )
  const className = `summary-card summary-card--${tone}`

  return href.startsWith(`${DJANGO_BASE}/`)
    ? <a className={className} href={href}>{content}</a>
    : <Link className={className} to={href}>{content}</Link>
}

function PageHeading({ eyebrow, title, description, action }) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {action}
    </div>
  )
}

function CourseGrid({ courses, emptyTitle = 'No courses to show', emptyDescription = 'Courses available to your account will appear here.' }) {
  if (!courses.length) {
    return (
      <div className="empty-state">
        <span className="empty-state-icon" aria-hidden="true">◎</span>
        <h3>{emptyTitle}</h3>
        <p>{emptyDescription}</p>
        <a className="button button-secondary" href={`${DJANGO_BASE}/courses/`}>Open course catalogue</a>
      </div>
    )
  }

  return <div className="course-grid">{courses.map((course) => <CourseCard course={course} key={course.id} />)}</div>
}

function ApiState({ loading, error, authRequired, retry }) {
  if (loading) {
    return (
      <div aria-live="polite" className="state-panel state-panel--loading">
        <span aria-hidden="true" className="loading-indicator" />
        <p>Loading live SGAMS data…</p>
      </div>
    )
  }
  if (error) {
    return (
      <div aria-live="polite" className="state-panel state-panel--error" role="alert">
        <div>
          <strong>Data is temporarily unavailable</strong>
          <p>{error}</p>
        </div>
        <div className="state-actions">
          {authRequired && <a className="button button-primary" href={`${DJANGO_BASE}/login/`}>Sign in</a>}
          <button className="button button-secondary" onClick={retry} type="button">Try again</button>
        </div>
      </div>
    )
  }
  return null
}

const taskStatusLabels = {
  todo: 'Not Started',
  in_progress: 'In Progress',
  under_review: 'Under Review',
  completed: 'Completed',
  overdue: 'Overdue',
}

function getTaskStatus(task) {
  return task.effective_status || task.status
}

function formatDate(value) {
  if (!value) return 'Not set'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Not set'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: value.includes('T') ? 'short' : undefined,
  }).format(date)
}

function lookup(items, id) {
  return items.find((item) => item.id === id)
}

function CourseName({ course }) {
  return course?.code || course?.name || 'Course details unavailable'
}

function TaskStatusBadge({ status }) {
  return (
    <span className={`status-badge task-status task-status--${status || 'unknown'}`}>
      <span aria-hidden="true" className="status-dot" />
      {taskStatusLabels[status] || status || 'Unknown'}
    </span>
  )
}

function AssignmentCard({ assignment, courses, groups }) {
  const course = lookup(courses, assignment.course)
  const group = groups.find((item) => item.assignment === assignment.id)

  return (
    <article className="portal-card assignment-card">
      <div className="course-card-top">
        <span className="course-code"><CourseName course={course} /></span>
        <span className={`status-badge assignment-status--${assignment.status || 'unknown'}`}>
          {assignment.status || 'Status unavailable'}
        </span>
      </div>
      <h3>{assignment.title}</h3>
      <p className="card-detail">Due {formatDate(assignment.deadline)}</p>
      <p className="card-detail">{group ? `Group: ${group.name}` : 'No group assigned yet'}</p>
      <Link className="text-action" to={`/assignments/${assignment.id}`}>
        Assignment details <span aria-hidden="true">→</span>
      </Link>
    </article>
  )
}

function GroupCard({ group, assignments, courses, tasks = [] }) {
  const assignment = lookup(assignments, group.assignment)
  const course = assignment && lookup(courses, assignment.course)
  const groupTasks = tasks.filter((task) => task.group === group.id)
  const progress = Number.isFinite(group.progress) ? group.progress : 0
  const courseLabel = group.course_code || group.course_name || course?.code || course?.name || 'Course'

  return (
    <article className="portal-card group-card">
      <div className="course-card-top">
        <span className="course-code">{courseLabel}</span>
        <span className="status-badge"><span aria-hidden="true" className="status-dot" />{group.status}</span>
      </div>
      <h3>{group.name}</h3>
      <p className="card-detail">{group.assignment_title || assignment?.title || 'Assignment details unavailable'}</p>
      <div className="group-progress-label">
        <span>Group task progress</span><strong>{progress}%</strong>
      </div>
      <div
        aria-label={`${progress}% of group tasks completed`}
        className="progress-track"
        role="img"
      >
        <span style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} />
      </div>
      <p className="card-detail">
        {group.members?.length ?? 0} members · {groupTasks.length} {groupTasks.length === 1 ? 'task' : 'tasks'}
      </p>
      <Link className="text-action" to={`/groups/${group.id}`}>
        View group <span aria-hidden="true">→</span>
      </Link>
    </article>
  )
}

function TaskCard({ task, groups, assignments }) {
  const group = lookup(groups, task.group)
  const assignment = lookup(assignments, task.assignment)
  const status = getTaskStatus(task)
  const priority = task.priority
    ? `${task.priority[0].toUpperCase()}${task.priority.slice(1)} priority`
    : ''

  return (
    <article className="portal-card task-card">
      <div className="course-card-top">
        <TaskStatusBadge status={status} />
        {priority && <span className={`priority-label priority--${task.priority}`}>{priority}</span>}
      </div>
      <h3>{task.title || task.description}</h3>
      <p className="card-detail">{task.assignment_title || assignment?.title || 'Assignment details unavailable'}</p>
      <p className="card-detail">Group: {task.group_name || group?.name || 'Group details unavailable'}</p>
      <p className="card-detail">Assigned to: {task.assigned_to_name || 'Group member'}</p>
      <p className="card-detail">Due {formatDate(task.deadline)}</p>
      <Link className="text-action" to={`/tasks/${task.id}`}>
        View task <span aria-hidden="true">→</span>
      </Link>
    </article>
  )
}

function DjangoPortalNotice({ role }) {
  return (
    <div className="page-content">
      <PageHeading eyebrow="Role-specific workspace" title="Continue in the SGAMS portal" />
      <div className="empty-state">
        <h3>This React workspace is currently for students</h3>
        <p>Your {role} account and permissions remain available in the existing Django portal.</p>
        <a className="button button-primary" href={`${DJANGO_BASE}/`}>Open Django portal</a>
      </div>
    </div>
  )
}

function EmptyPanel({ title, description, action }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  )
}

function DashboardPage({ user }) {
  const courses = useApiList('courses')
  const groups = useApiList('groups')
  const assignments = useApiList('assignments')
  const tasks = useApiList('tasks')
  const activeCourses = useMemo(() => courses.data.filter((course) => course.is_active), [courses.data])
  const assignedTasks = useMemo(
    () => tasks.data.filter((task) => task.assigned_to === user.id),
    [tasks.data, user.id],
  )
  const completedTasks = assignedTasks.filter((task) => task.status === 'completed')
  const pendingTasks = assignedTasks.filter((task) => task.status !== 'completed')
  const activeAssignments = assignments.data.filter((assignment) => assignment.status === 'published')
  const upcomingDeadlines = assignedTasks.filter(
    (task) => task.status !== 'completed' && getTaskStatus(task) !== 'overdue' && task.deadline,
  )
  const requiredLists = [courses, groups, assignments, tasks]
  const dataError = requiredLists.find((list) => list.error)
  const dataLoading = requiredLists.some((list) => list.loading)
  const retryData = () => requiredLists.forEach((list) => list.error && list.retry())
  const metricValue = (value) => dataLoading || dataError ? '—' : value

  return (
    <>
      <section className="welcome-banner">
        <div className="welcome-content">
          <p className="eyebrow eyebrow--light">Student Group Assignment Management System</p>
          <h2>Plan together.<br /><span>Make progress visible.</span></h2>
          <p>Keep group responsibilities, milestones and contributions in one organised workspace.</p>
          <a className="button button-light" href={`${DJANGO_BASE}/groups/`}>Explore group workspaces <span aria-hidden="true">→</span></a>
        </div>
        <div aria-hidden="true" className="welcome-art">
          <span className="orbit orbit-one" />
          <span className="orbit orbit-two" />
          <span className="orbit orbit-three" />
          <span className="art-monogram">SG</span>
        </div>
      </section>

      <div className="page-content">
        <PageHeading
          description="A clear starting point for your courses and collaborative work."
          eyebrow="Your workspace"
          title="Dashboard"
        />
        <div aria-label="Workspace shortcuts" className="summary-grid">
          <SummaryCard
            detail="Courses available to your account"
            href="/courses"
            label="My Courses"
            tone="blue"
            value={metricValue(activeCourses.length)}
          />
          <SummaryCard
            detail="Your approved group memberships"
            href="/groups"
            label="My Groups"
            tone="teal"
            value={metricValue(groups.data.length)}
          />
          <SummaryCard
            detail="Published for your courses"
            href="/assignments"
            label="Active Assignments"
            tone="gold"
            value={metricValue(activeAssignments.length)}
          />
          <SummaryCard
            detail="Tasks assigned to you"
            href="/tasks?scope=mine"
            label="My Tasks"
            tone="blue"
            value={metricValue(assignedTasks.length)}
          />
          <SummaryCard
            detail="Not yet completed"
            href="/tasks?scope=mine&status=pending"
            label="Pending Tasks"
            tone="teal"
            value={metricValue(pendingTasks.length)}
          />
          <SummaryCard
            detail="Marked completed"
            href="/tasks?scope=mine&status=completed"
            label="Completed Tasks"
            tone="gold"
            value={metricValue(completedTasks.length)}
          />
          <SummaryCard
            detail="Tasks with future due dates"
            href="/tasks?scope=mine&status=upcoming"
            label="Upcoming Deadlines"
            tone="blue"
            value={metricValue(upcomingDeadlines.length)}
          />
        </div>

        {dataError && <ApiState
          authRequired={dataError.authRequired}
          error={dataError.error}
          loading={dataLoading}
          retry={retryData}
        />}
        <section aria-labelledby="course-preview-title" className="section-block">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Learning spaces</p>
              <h2 id="course-preview-title">My Courses</h2>
            </div>
            <Link className="text-action" to="/courses">View all courses <span aria-hidden="true">→</span></Link>
          </div>
          {!dataError && (dataLoading
            ? <ApiState loading />
            : <CourseGrid courses={activeCourses.slice(0, 3)} />)}
        </section>

        <section aria-labelledby="group-preview-title" className="section-block">
          <div className="section-heading">
            <div><p className="eyebrow">Collaborative spaces</p><h2 id="group-preview-title">My Groups</h2></div>
            <Link className="text-action" to="/groups">View all groups <span aria-hidden="true">→</span></Link>
          </div>
          {!dataError && !dataLoading && <div className="course-grid">
            {groups.data.slice(0, 3).map((group) => (
              <GroupCard
                assignments={assignments.data}
                courses={courses.data}
                group={group}
                key={group.id}
                tasks={tasks.data}
              />
            ))}
            {!groups.data.length && <EmptyPanel title="No groups yet" description="Approved group workspaces will appear here." />}
          </div>}
        </section>

        <section aria-labelledby="task-preview-title" className="section-block">
          <div className="section-heading">
            <div><p className="eyebrow">Your responsibilities</p><h2 id="task-preview-title">My Tasks</h2></div>
            <Link className="text-action" to="/tasks">View all tasks <span aria-hidden="true">→</span></Link>
          </div>
          {!dataError && !dataLoading && <div className="course-grid">
            {assignedTasks.slice(0, 3).map((task) => (
              <TaskCard assignments={assignments.data} groups={groups.data} key={task.id} task={task} />
            ))}
            {!assignedTasks.length && <EmptyPanel title="No tasks assigned" description="Tasks assigned to you will appear here." />}
          </div>}
        </section>

        <section aria-labelledby="principles-title" className="principles-strip">
          <div>
            <span aria-hidden="true" className="principle-icon">01</span>
            <h3 id="principles-title">Responsibility</h3>
            <p>Make individual ownership and deadlines easy to find.</p>
          </div>
          <div>
            <span aria-hidden="true" className="principle-icon">02</span>
            <h3>Visibility</h3>
            <p>See how group work is progressing over time.</p>
          </div>
          <div>
            <span aria-hidden="true" className="principle-icon">03</span>
            <h3>Accountability</h3>
            <p>Review activity records as context, not as proof of effort or quality.</p>
          </div>
        </section>
      </div>
    </>
  )
}

function CoursePage() {
  const [query, setQuery] = useState('')
  const [yearOfStudy, setYearOfStudy] = useState('')
  const [semester, setSemester] = useState('')
  const courses = useApiList('courses')
  const filteredCourses = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return courses.data.filter((course) => {
      const searchableText = [
        course.code,
        course.name,
        course.programme,
        course.year_of_study_label,
        course.semester_label,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return searchableText.includes(normalizedQuery)
        && (!yearOfStudy || String(course.year_of_study || '') === yearOfStudy)
        && (!semester || String(course.semester || '') === semester)
    })
  }, [courses.data, query, semester, yearOfStudy])

  return (
    <div className="page-content">
      <PageHeading
        description="Browse courses available to your account and open their group-assignment workspaces."
        eyebrow="Learning spaces"
        title="My Courses"
      />
      <div className="toolbar">
        <label className="search-field">
          <span aria-hidden="true" className="search-icon">⌕</span>
          <span className="visually-hidden">Search courses</span>
          <input onChange={(event) => setQuery(event.target.value)} placeholder="Search courses" type="search" value={query} />
        </label>
        <span className="result-count" aria-live="polite">
          {courses.loading
            ? 'Loading courses'
            : courses.error
              ? 'Courses unavailable'
              : `${filteredCourses.length} ${filteredCourses.length === 1 ? 'course' : 'courses'}`}
        </span>
      </div>
      <div className="course-management-filters">
        <label>Year of study
          <select className="admin-control" onChange={(event) => setYearOfStudy(event.target.value)} value={yearOfStudy}>
            <option value="">All years of study</option>
            {YEAR_OF_STUDY_OPTIONS.map((year) => <option key={year} value={year}>Year {year}</option>)}
          </select>
        </label>
        <label>Semester
          <select className="admin-control" onChange={(event) => setSemester(event.target.value)} value={semester}>
            <option value="">All semesters</option>
            {SEMESTER_OPTIONS.map((item) => <option key={item} value={item}>Semester {item}</option>)}
          </select>
        </label>
      </div>
      {courses.error || courses.loading
        ? <ApiState
            authRequired={courses.authRequired}
            error={courses.error}
            loading={courses.loading}
            retry={courses.retry}
          />
        : <CourseGrid
            courses={filteredCourses}
            emptyDescription={query || yearOfStudy || semester ? 'Try changing or clearing the search and filters.' : undefined}
            emptyTitle={query || yearOfStudy || semester ? 'No matching courses' : undefined}
          />}
    </div>
  )
}

function CourseDetailPage() {
  const { id } = useParams()
  const course = useApiRecord(`courses/${id}`)
  const assignments = useApiList('assignments')
  const groups = useApiList('groups')

  if (
    course.loading || course.error
    || assignments.loading || assignments.error
    || groups.loading || groups.error
  ) {
    const resource = course.error || course.loading
      ? course
      : assignments.error || assignments.loading ? assignments : groups
    return <div className="page-content"><ApiState
      authRequired={resource.authRequired}
      error={resource.error}
      loading={resource.loading}
      retry={resource.retry}
    /></div>
  }

  const courseAssignments = assignments.data.filter((assignment) => assignment.course === course.data.id)
  return (
    <div className="page-content">
      <Link className="back-link" to="/courses">← Back to My Courses</Link>
      <PageHeading
        description={[
          course.data.programme,
          course.data.year_of_study_label,
          course.data.semester_label,
        ].filter(Boolean).join(' · ')}
        eyebrow={course.data.code || 'Course'}
        title={course.data.name}
      />
      <section className="section-block">
        <div className="section-heading"><div><p className="eyebrow">Course work</p><h2>Assignments</h2></div></div>
        <div className="course-grid">
          {courseAssignments.map((assignment) => (
            <AssignmentCard
              assignment={assignment}
              courses={[course.data]}
              groups={groups.data}
              key={assignment.id}
            />
          ))}
          {!courseAssignments.length && <EmptyPanel
            title="No published assignments"
            description="Assignments made available to your account will appear here."
          />}
        </div>
      </section>
    </div>
  )
}

function GroupListPage() {
  const groups = useApiList('groups')
  const assignments = useApiList('assignments')
  const courses = useApiList('courses')
  const tasks = useApiList('tasks')
  const resources = [groups, assignments, courses, tasks]
  const failed = resources.find((resource) => resource.error)
  const loading = resources.some((resource) => resource.loading)

  if (loading || failed) {
    return <div className="page-content"><ApiState
      authRequired={failed?.authRequired}
      error={failed?.error}
      loading={loading}
      retry={() => resources.forEach((resource) => resource.error && resource.retry())}
    /></div>
  }

  return (
    <div className="page-content">
      <PageHeading
        description="Approved group workspaces, member rosters and shared task progress."
        eyebrow="Collaborative spaces"
        title="My Groups"
      />
      <div className="course-grid">
        {groups.data.map((group) => (
          <GroupCard
            assignments={assignments.data}
            courses={courses.data}
            group={group}
            key={group.id}
            tasks={tasks.data}
          />
        ))}
        {!groups.data.length && <EmptyPanel
          title="No groups yet"
          description="Once you join an approved assignment group, it will appear here."
          action={<a className="button button-secondary" href={`${DJANGO_BASE}/assignments/`}>Browse assignments</a>}
        />}
      </div>
    </div>
  )
}

function GroupDetailPage() {
  const { id } = useParams()
  const group = useApiRecord(`groups/${id}`)
  const tasks = useApiList('tasks')
  const resources = [group, tasks]
  const failed = resources.find((resource) => resource.error)
  const loading = resources.some((resource) => resource.loading)

  if (loading || failed) {
    return <div className="page-content"><ApiState
      authRequired={failed?.authRequired}
      error={failed?.error}
      loading={loading}
      retry={() => resources.forEach((resource) => resource.error && resource.retry())}
    /></div>
  }

  const groupData = group.data
  const groupTasks = tasks.data.filter((task) => task.group === groupData.id)

  return (
    <div className="page-content">
      <Link className="back-link" to="/groups">← Back to My Groups</Link>
      <PageHeading
        description={groupData.description || groupData.assignment_title || 'Group assignment workspace'}
        eyebrow={groupData.course_code || 'Group workspace'}
        title={groupData.name}
        action={<a className="button button-primary" href={`${DJANGO_BASE}/groups/${groupData.id}/`}>Open full group workspace</a>}
      />
      <div className="detail-grid">
        <section className="detail-card">
          <p className="eyebrow">Assignment</p>
          <h2>{groupData.assignment_title || 'Assignment details unavailable'}</h2>
          <p>{groupData.course_name || 'Course details unavailable'}</p>
          <p className="card-detail">Submission deadline: {formatDate(groupData.assignment_deadline)}</p>
        </section>
        <section className="detail-card">
          <p className="eyebrow">Group progress</p>
          <p className="detail-stat">{groupData.progress ?? 0}%</p>
          <div
            aria-label={`${groupData.progress ?? 0}% of group tasks completed`}
            className="progress-track progress-track--large"
            role="img"
          >
            <span style={{ width: `${Math.min(100, Math.max(0, groupData.progress ?? 0))}%` }} />
          </div>
          <p className="card-detail">{groupTasks.filter((task) => task.status === 'completed').length} of {groupTasks.length} tasks completed</p>
        </section>
      </div>

      <section aria-labelledby="group-members-title" className="section-block">
        <div className="section-heading">
          <div><p className="eyebrow">Your team</p><h2 id="group-members-title">Group members</h2></div>
          <span className="result-count">{groupData.members?.length ?? 0} members</span>
        </div>
        <div className="member-list">
          {(groupData.members || []).map((member) => (
            <div className="member-row" key={member.id}>
              <span aria-hidden="true" className="user-avatar">{member.full_name.slice(0, 1).toUpperCase()}</span>
              <strong>{member.full_name}</strong>
              {member.is_leader && <span className="member-leader">Group leader</span>}
            </div>
          ))}
          {!groupData.members?.length && <EmptyPanel title="No member details" description="The group member roster is unavailable." />}
        </div>
      </section>

      <section aria-labelledby="group-tasks-title" className="section-block">
        <div className="section-heading">
          <div><p className="eyebrow">Shared responsibilities</p><h2 id="group-tasks-title">Group tasks</h2></div>
          <Link className="text-action" to={`/tasks?scope=group&group=${groupData.id}`}>View task board <span aria-hidden="true">→</span></Link>
        </div>
        <div className="course-grid">
          {groupTasks.map((task) => <TaskCard assignments={[]} groups={[groupData]} key={task.id} task={task} />)}
          {!groupTasks.length && <EmptyPanel title="No tasks yet" description="Tasks added to this group will appear here." />}
        </div>
      </section>
    </div>
  )
}

function AssignmentListPage() {
  const [query, setQuery] = useState('')
  const assignments = useApiList('assignments')
  const groups = useApiList('groups')
  const courses = useApiList('courses')
  const resources = [assignments, groups, courses]
  const failed = resources.find((resource) => resource.error)
  const loading = resources.some((resource) => resource.loading)
  const filtered = assignments.data.filter((assignment) => {
    const group = groups.data.find((item) => item.assignment === assignment.id)
    const course = lookup(courses.data, assignment.course)
    return [assignment.title, course?.name, course?.code, group?.name]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(query.trim().toLowerCase())
  })

  return (
    <div className="page-content">
      <PageHeading
        description="Published assignment briefs, course links, group allocation and submission deadlines."
        eyebrow="Course work"
        title="My Assignments"
      />
      <SearchToolbar
        count={loading || failed ? null : filtered.length}
        onChange={setQuery}
        placeholder="Search assignments"
        value={query}
      />
      {loading || failed
        ? <ApiState
            authRequired={failed?.authRequired}
            error={failed?.error}
            loading={loading}
            retry={() => resources.forEach((resource) => resource.error && resource.retry())}
          />
        : <div className="course-grid">
            {filtered.map((assignment) => (
              <AssignmentCard assignment={assignment} courses={courses.data} groups={groups.data} key={assignment.id} />
            ))}
            {!filtered.length && <EmptyPanel
              title={query ? 'No matching assignments' : 'No assignments yet'}
              description="Published assignments available to your account will appear here."
            />}
          </div>}
      <p className="scope-note">
        Submission status, uploads and lecturer feedback are available in each assignment’s existing Django workspace.
      </p>
    </div>
  )
}

function AssignmentDetailPage() {
  const { id } = useParams()
  const assignment = useApiRecord(`assignments/${id}`)
  const courses = useApiList('courses')
  const groups = useApiList('groups')

  if (assignment.loading || assignment.error || courses.loading || courses.error || groups.loading || groups.error) {
    const resource = assignment.error || assignment.loading
      ? assignment
      : courses.error || courses.loading ? courses : groups
    return <div className="page-content"><ApiState
      authRequired={resource.authRequired}
      error={resource.error}
      loading={resource.loading}
      retry={resource.retry}
    /></div>
  }

  const course = lookup(courses.data, assignment.data.course)
  const groupsForAssignment = groups.data.filter((group) => group.assignment === assignment.data.id)

  return (
    <div className="page-content">
      <Link className="back-link" to="/assignments">← Back to My Assignments</Link>
      <PageHeading
        description={course?.name || 'Course details unavailable'}
        eyebrow={course?.code || 'Assignment'}
        title={assignment.data.title}
        action={<a className="button button-primary" href={`${DJANGO_BASE}/assignments/${assignment.data.id}/`}>Open assignment workspace</a>}
      />
      <div className="detail-grid">
        <section className="detail-card">
          <p className="eyebrow">Brief</p>
          <h2>Assignment overview</h2>
          <p className="preserve-lines">{assignment.data.description}</p>
          {assignment.data.objectives && <><h3>Objectives</h3><p className="preserve-lines">{assignment.data.objectives}</p></>}
        </section>
        <section className="detail-card">
          <p className="eyebrow">Key dates</p>
          <h2>{formatDate(assignment.data.deadline)}</h2>
          <p>Submission deadline</p>
          <span className={`status-badge assignment-status--${assignment.data.status}`}>
            {assignment.data.status}
          </span>
        </section>
      </div>
      <section className="section-block">
        <div className="section-heading"><div><p className="eyebrow">Collaborative workspace</p><h2>My group</h2></div></div>
        <div className="course-grid">
          {groupsForAssignment.map((group) => (
            <GroupCard assignments={[assignment.data]} courses={course ? [course] : []} group={group} key={group.id} />
          ))}
          {!groupsForAssignment.length && <EmptyPanel
            title="No group assigned"
            description="Group membership and proposal options are managed in the Django assignment workspace."
            action={<a className="button button-secondary" href={`${DJANGO_BASE}/assignments/${assignment.data.id}/`}>Open Django assignment</a>}
          />}
        </div>
      </section>
    </div>
  )
}

function SearchToolbar({ count, onChange, placeholder, value }) {
  return (
    <div className="toolbar">
      <label className="search-field">
        <span aria-hidden="true" className="search-icon">⌕</span>
        <span className="visually-hidden">{placeholder}</span>
        <input onChange={(event) => onChange(event.target.value)} placeholder={placeholder} type="search" value={value} />
      </label>
      <span aria-live="polite" className="result-count">{count === null ? 'Loading records' : `${count} ${count === 1 ? 'result' : 'results'}`}</span>
    </div>
  )
}

function TaskListPage({ user }) {
  const tasks = useApiList('tasks')
  const groups = useApiList('groups')
  const assignments = useApiList('assignments')
  const [searchParams, setSearchParams] = useSearchParams()
  const scope = searchParams.get('scope') === 'group' ? 'group' : 'mine'
  const statusFilter = searchParams.get('status') || 'all'
  const groupFilter = Number(searchParams.get('group')) || null
  const resources = [tasks, groups, assignments]
  const failed = resources.find((resource) => resource.error)
  const loading = resources.some((resource) => resource.loading)
  const visibleTasks = filterTasks(tasks.data, {
    scope,
    userId: user.id,
    statusFilter,
    groupFilter,
  })
  const updateQuery = (key, value) => {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(key, value)
    else next.delete(key)
    setSearchParams(next)
  }

  return (
    <div className="page-content">
      <PageHeading
        description="See your own responsibilities or the wider task board for your approved groups."
        eyebrow="Responsibility and progress"
        title={scope === 'mine' ? 'My Tasks' : 'Group Tasks'}
      />
      <div aria-label="Task scope" className="segmented-control">
        <button aria-pressed={scope === 'mine'} onClick={() => updateQuery('scope', 'mine')} type="button">My Tasks</button>
        <button aria-pressed={scope === 'group'} onClick={() => updateQuery('scope', 'group')} type="button">Group Tasks</button>
      </div>
      <div className="task-filters">
        <label>
          <span>Filter by status</span>
          <select onChange={(event) => updateQuery('status', event.target.value === 'all' ? '' : event.target.value)} value={statusFilter}>
            <option value="all">All statuses</option>
            <option value="pending">Pending</option>
            <option value="todo">Not Started</option>
            <option value="in_progress">In Progress</option>
            <option value="under_review">Under Review</option>
            <option value="completed">Completed</option>
            <option value="overdue">Overdue</option>
            <option value="upcoming">Due in 7 days</option>
          </select>
        </label>
        {groupFilter && <button className="button button-secondary" onClick={() => updateQuery('group', '')} type="button">Clear group filter</button>}
      </div>
      {loading || failed
        ? <ApiState
            authRequired={failed?.authRequired}
            error={failed?.error}
            loading={loading}
            retry={() => resources.forEach((resource) => resource.error && resource.retry())}
          />
        : <div className="course-grid">
            {visibleTasks.map((task) => <TaskCard assignments={assignments.data} groups={groups.data} key={task.id} task={task} />)}
            {!visibleTasks.length && <EmptyPanel
              title={scope === 'mine' ? 'No tasks match these filters' : 'No group tasks match these filters'}
              description="Tasks visible to your account will appear here."
            />}
          </div>}
    </div>
  )
}

function TaskDetailPage({ user }) {
  const { id } = useParams()
  const task = useApiRecord(`tasks/${id}`)
  const taskData = task.data
  const group = useApiRecord(taskData?.group ? `groups/${taskData.group}` : '')
  const updates = useApiList('taskupdates')
  const resources = [task, group, updates]
  const failed = resources.find((resource) => resource.error)
  const loading = resources.some((resource) => resource.loading)

  if (loading || failed) {
    return <div className="page-content"><ApiState
      authRequired={failed?.authRequired}
      error={failed?.error}
      loading={loading}
      retry={() => resources.forEach((resource) => resource.error && resource.retry())}
    /></div>
  }

  const groupData = group.data
  const assignee = groupData.members?.find((member) => member.id === taskData.assigned_to)
  const authoredUpdates = updates.data
    .filter((update) => update.task === taskData.id)
    .sort((left, right) => new Date(right.timestamp) - new Date(left.timestamp))
  const status = getTaskStatus(taskData)

  return (
    <div className="page-content">
      <Link className="back-link" to={`/tasks?scope=${taskData.assigned_to === user.id ? 'mine' : 'group'}`}>← Back to tasks</Link>
      <PageHeading
        description={taskData.description}
        eyebrow={groupData.name}
        title={taskData.title || taskData.description}
        action={<a className="button button-primary" href={`${DJANGO_BASE}/tasks/${taskData.id}/`}>Update task progress</a>}
      />
      <div className="detail-grid">
        <section className="detail-card">
          <p className="eyebrow">Responsibility</p>
          <h2>{assignee?.full_name || taskData.assigned_to_name || 'Group member'}</h2>
          <p>Assigned student</p>
          <p className="card-detail">Group: <Link to={`/groups/${groupData.id}`}>{groupData.name}</Link></p>
          <p className="card-detail">Assignment: {taskData.assignment_title}</p>
          <p className="card-detail">Course: {taskData.course_name}</p>
        </section>
        <section className="detail-card">
          <p className="eyebrow">Deadline and progress</p>
          <h2>{formatDate(taskData.deadline)}</h2>
          <p>Task deadline</p>
          <TaskStatusBadge status={status} />
          <p className="card-detail">Priority: {taskData.priority || 'Normal'}</p>
          <p className="card-detail">Created by: {taskData.created_by_name || 'Not recorded'}</p>
          <p className="card-detail">Created: {formatDate(taskData.created_at)}</p>
          <p className="card-detail">Last updated: {formatDate(taskData.updated_at)}</p>
        </section>
      </div>
      <section aria-labelledby="task-updates-title" className="section-block">
        <div className="section-heading">
          <div><p className="eyebrow">Progress record</p><h2 id="task-updates-title">Task updates</h2></div>
        </div>
        <div className="update-list">
          {authoredUpdates.map((update) => (
            <article className="update-entry" key={update.id}>
              <div className="update-entry-heading">
                <strong>{update.author_name || 'Former member'}</strong>
                <time dateTime={update.timestamp}>{formatDate(update.timestamp)}</time>
              </div>
              <p>{update.update_text}</p>
            </article>
          ))}
          {!authoredUpdates.length && <EmptyPanel
            title="No progress updates yet"
            description="Task updates can be added from the existing Django task workspace."
          />}
        </div>
        <p className="scope-note">Activity records provide context and are not definitive proof of academic effort or work quality.</p>
      </section>
    </div>
  )
}

function formatMutationError(error) {
  const responseData = error.response?.data
  if (typeof responseData?.detail === 'string') return responseData.detail
  if (responseData && typeof responseData === 'object') {
    return Object.entries(responseData)
      .map(([field, messages]) => `${field}: ${Array.isArray(messages) ? messages.join(' ') : messages}`)
      .join(' ')
  }
  return 'The change could not be saved. Please try again.'
}

function AdminDashboardPage() {
  const summary = useApiRecord('admin/summary')
  const activity = useApiList('admin/activity')
  const resources = [summary, activity]
  const failed = resources.find((resource) => resource.error)
  const loading = resources.some((resource) => resource.loading)

  return (
    <>
      <section className="welcome-banner">
        <div className="welcome-content">
          <p className="eyebrow eyebrow--light">System administration</p>
          <h2>Manage SGAMS.<br /><span>Keep the system accountable.</span></h2>
          <p>Manage users and roles, maintain courses, and review system activity and reports.</p>
        </div>
        <div aria-hidden="true" className="welcome-art">
          <span className="orbit orbit-one" />
          <span className="orbit orbit-two" />
          <span className="orbit orbit-three" />
          <span className="art-monogram">SG</span>
        </div>
      </section>
      <div className="page-content">
        <PageHeading
          description="Live system totals and links to role-protected administration workflows."
          eyebrow="Administrator workspace"
          title="Administration dashboard"
        />
        {loading || failed
          ? <ApiState
              authRequired={failed?.authRequired}
              error={failed?.error}
              loading={loading}
              retry={() => resources.forEach((resource) => resource.error && resource.retry())}
            />
          : <div className="summary-grid admin-summary-grid">
              <SummaryCard detail={`${summary.data.users.active} active`} href="/users" label="Users" tone="blue" value={summary.data.users.total} />
              <SummaryCard detail="Student accounts" href="/users" label="Students" tone="teal" value={summary.data.users.students} />
              <SummaryCard detail="Teaching accounts" href="/users" label="Lecturers" tone="gold" value={summary.data.users.lecturers} />
              <SummaryCard detail="System administrators" href="/users" label="Administrators" tone="blue" value={summary.data.users.administrators} />
              <SummaryCard detail={`${summary.data.courses.active} active`} href="/courses" label="Courses" tone="teal" value={summary.data.courses.total} />
              <SummaryCard detail="Published assignments" href={`${DJANGO_BASE}/assignments/`} label="Assignments" tone="gold" value={summary.data.assignments.published} />
              <SummaryCard detail={`${summary.data.groups.pending} awaiting approval`} href={`${DJANGO_BASE}/groups/`} label="Groups" tone="blue" value={summary.data.groups.total} />
              <SummaryCard detail={`${summary.data.submissions.latest} latest submissions recorded`} href={`${DJANGO_BASE}/submissions/`} label="Awaiting review" tone="teal" value={summary.data.submissions.awaiting_review} />
              <SummaryCard detail="Recorded in the last 30 days" href="/activity" label="Recent actions" tone="gold" value={summary.data.activity.recent_actions} />
            </div>}

        <section className="section-block">
          <div className="section-heading">
            <div><p className="eyebrow">Administration</p><h2>Management tools</h2></div>
          </div>
          <div className="admin-action-grid">
            <Link className="detail-card admin-action-card" to="/users"><strong>User and role management</strong><span>Provision accounts and update roles or access.</span></Link>
            <Link className="detail-card admin-action-card" to="/courses"><strong>Course management</strong><span>Create courses and maintain course assignments.</span></Link>
            <Link className="detail-card admin-action-card" to="/activity"><strong>System activity</strong><span>Review recent recorded actions and audit events.</span></Link>
            <Link className="detail-card admin-action-card" to="/reports"><strong>Administrative reports</strong><span>Open activity, course, and system summary reports.</span></Link>
          </div>
        </section>

        <section className="section-block">
          <div className="section-heading">
            <div><p className="eyebrow">Audit trail</p><h2>Recent system activity</h2></div>
            <Link className="text-action" to="/activity">View all activity <span aria-hidden="true">→</span></Link>
          </div>
          {loading || failed
            ? <ApiState
                authRequired={failed?.authRequired}
                error={failed?.error}
                loading={loading}
                retry={() => resources.forEach((resource) => resource.error && resource.retry())}
              />
            : <ActivityTable activities={activity.data.slice(0, 8)} />}
        </section>
      </div>
    </>
  )
}

function UserRoleRow({ user, currentUserId, onSaved }) {
  const [role, setRole] = useState(user.role)
  const [isActive, setIsActive] = useState(user.is_active)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const isCurrentUser = user.id === currentUserId

  async function saveUser(event) {
    event.preventDefault()
    setSaving(true)
    setError('')
    setNotice('')
    try {
      await apiClient.patch(`users/${user.id}/`, { role, is_active: isActive })
      setNotice('Saved')
      onSaved()
    } catch (requestError) {
      setError(formatMutationError(requestError))
    } finally {
      setSaving(false)
    }
  }

  const fullName = [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username
  return (
    <tr>
      <th scope="row">{fullName}<div className="admin-user-subtitle">{user.username}</div></th>
      <td>{user.email || '—'}</td>
      <td>
        <form className="admin-inline-form" onSubmit={saveUser}>
          <label className="visually-hidden" htmlFor={`role-${user.id}`}>Role for {user.username}</label>
          <select
            className="admin-control"
            disabled={isCurrentUser || saving}
            id={`role-${user.id}`}
            onChange={(event) => setRole(event.target.value)}
            value={role}
          >
            <option value="student">Student</option>
            <option value="lecturer">Lecturer</option>
            <option value="admin">Administrator</option>
          </select>
          <label className="admin-checkbox">
            <input
              checked={isActive}
              disabled={isCurrentUser || saving}
              onChange={(event) => setIsActive(event.target.checked)}
              type="checkbox"
            />
            Active
          </label>
          <button className="button button-secondary button-small" disabled={saving} type="submit">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </form>
        {isCurrentUser && <small className="admin-user-subtitle">Your role and access are protected.</small>}
        {error && <p className="form-feedback form-feedback--error" role="alert">{error}</p>}
        {notice && <p className="form-feedback" aria-live="polite">{notice}</p>}
      </td>
      <td><StatusBadge active={user.is_active} /></td>
    </tr>
  )
}

function UserPage({ user: currentUser }) {
  const users = useApiList('users')
  const [search, setSearch] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [newUser, setNewUser] = useState({
    username: '',
    first_name: '',
    last_name: '',
    email: '',
    role: 'student',
    password: '',
  })
  const visibleUsers = users.data.filter((user) => (
    [user.username, user.first_name, user.last_name, user.email, user.role]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(search.trim().toLowerCase())
  ))

  async function createUser(event) {
    event.preventDefault()
    setCreating(true)
    setCreateError('')
    try {
      await apiClient.post('users/', newUser)
      setNewUser({
        username: '',
        first_name: '',
        last_name: '',
        email: '',
        role: 'student',
        password: '',
      })
      users.retry()
    } catch (error) {
      setCreateError(formatMutationError(error))
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="page-content">
      <PageHeading
        description="Create accounts and maintain roles and access. Account changes are enforced by the Django administrator API."
        eyebrow="Administration"
        title="Users and roles"
      />
      <section className="admin-panel section-block">
        <div className="section-heading">
          <div><p className="eyebrow">Provision account</p><h2>Create a user</h2></div>
        </div>
        <form className="admin-form-grid" onSubmit={createUser}>
          <label>Username<input autoComplete="username" className="admin-control" onChange={(event) => setNewUser({ ...newUser, username: event.target.value })} required value={newUser.username} /></label>
          <label>First name<input autoComplete="given-name" className="admin-control" onChange={(event) => setNewUser({ ...newUser, first_name: event.target.value })} value={newUser.first_name} /></label>
          <label>Last name<input autoComplete="family-name" className="admin-control" onChange={(event) => setNewUser({ ...newUser, last_name: event.target.value })} value={newUser.last_name} /></label>
          <label>Email<input autoComplete="email" className="admin-control" onChange={(event) => setNewUser({ ...newUser, email: event.target.value })} type="email" value={newUser.email} /></label>
          <label>Role<select className="admin-control" onChange={(event) => setNewUser({ ...newUser, role: event.target.value })} value={newUser.role}><option value="student">Student</option><option value="lecturer">Lecturer</option><option value="admin">Administrator</option></select></label>
          <label>Initial password<input autoComplete="new-password" className="admin-control" minLength="8" onChange={(event) => setNewUser({ ...newUser, password: event.target.value })} required type="password" value={newUser.password} /></label>
          <div className="admin-form-actions">
            <button className="button button-primary" disabled={creating} type="submit">{creating ? 'Creating…' : 'Create user'}</button>
          </div>
        </form>
        {createError && <p className="form-feedback form-feedback--error" role="alert">{createError}</p>}
      </section>
      <div className="admin-list-toolbar">
        <SearchToolbar count={users.loading || users.error ? null : visibleUsers.length} onChange={setSearch} placeholder="Search users" value={search} />
      </div>
      {users.error || users.loading
        ? <ApiState
            authRequired={users.authRequired}
            error={users.error}
            loading={users.loading}
            retry={users.retry}
          />
        : visibleUsers.length
          ? <div className="admin-table-wrap"><table className="admin-table">
              <thead><tr><th scope="col">User</th><th scope="col">Email</th><th scope="col">Role and access</th><th scope="col">Status</th></tr></thead>
              <tbody>{visibleUsers.map((entry) => <UserRoleRow currentUserId={currentUser.id} key={entry.id} onSaved={users.retry} user={entry} />)}</tbody>
            </table></div>
          : <EmptyPanel title={search ? 'No matching users' : 'No users to show'} description="No user records matched this search." />}
    </div>
  )
}

function AdminCourseForm({
  course,
  lecturers,
  onCancel,
  onSaved,
}) {
  const [draft, setDraft] = useState(() => ({
    code: course?.code || '',
    name: course?.name || '',
    programme: course?.programme || '',
    year_of_study: course?.year_of_study ? String(course.year_of_study) : '',
    semester: course?.semester ? String(course.semester) : '',
    lecturer: course?.lecturer ? String(course.lecturer) : '',
    is_active: course?.is_active ?? true,
  }))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  function setField(field, value) {
    setDraft((current) => ({ ...current, [field]: value }))
  }

  async function saveCourse(event) {
    event.preventDefault()
    setSaving(true)
    setError('')
    const payload = {
      code: draft.code,
      name: draft.name,
      programme: draft.programme,
      year_of_study: Number(draft.year_of_study),
      semester: Number(draft.semester),
      lecturer: draft.lecturer ? Number(draft.lecturer) : null,
      is_active: draft.is_active,
    }
    try {
      if (course) await apiClient.patch(`courses/${course.id}/`, payload)
      else await apiClient.post('courses/', payload)
      onSaved()
    } catch (requestError) {
      setError(formatMutationError(requestError))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="admin-panel section-block">
      <div className="section-heading">
        <div><p className="eyebrow">Course setup</p><h2>{course ? `Edit ${course.code || course.name}` : 'Create a course'}</h2></div>
        {course && <button className="button button-secondary button-small" onClick={onCancel} type="button">Cancel edit</button>}
      </div>
      <form className="admin-form-grid" onSubmit={saveCourse}>
        <label>Course code<input className="admin-control" onChange={(event) => setField('code', event.target.value)} value={draft.code} /></label>
        <label>Course name<input className="admin-control" onChange={(event) => setField('name', event.target.value)} required value={draft.name} /></label>
        <label>School / Department<input className="admin-control" onChange={(event) => setField('programme', event.target.value)} value={draft.programme} /></label>
        <label>Year of study
          <select className="admin-control" onChange={(event) => setField('year_of_study', event.target.value)} required value={draft.year_of_study}>
            <option value="">Select year of study</option>
            {YEAR_OF_STUDY_OPTIONS.map((year) => <option key={year} value={year}>Year {year}</option>)}
          </select>
        </label>
        <label>Semester
          <select className="admin-control" onChange={(event) => setField('semester', event.target.value)} required value={draft.semester}>
            <option value="">Select semester</option>
            {SEMESTER_OPTIONS.map((semester) => <option key={semester} value={semester}>Semester {semester}</option>)}
          </select>
        </label>
        <label>Lead lecturer<select className="admin-control" onChange={(event) => setField('lecturer', event.target.value)} value={draft.lecturer}><option value="">Unassigned</option>{lecturers.map((lecturer) => <option key={lecturer.id} value={lecturer.id}>{[lecturer.first_name, lecturer.last_name].filter(Boolean).join(' ') || lecturer.username}</option>)}</select></label>
        <label className="admin-checkbox"><input checked={draft.is_active} onChange={(event) => setField('is_active', event.target.checked)} type="checkbox" />Course is active</label>
        <div className="admin-form-actions"><button className="button button-primary" disabled={saving} type="submit">{saving ? 'Saving…' : course ? 'Save course' : 'Create course'}</button></div>
      </form>
      {error && <p className="form-feedback form-feedback--error" role="alert">{error}</p>}
    </section>
  )
}

function CourseManagementPage() {
  const courses = useApiList('courses')
  const users = useApiList('users')
  const [editing, setEditing] = useState(null)
  const [filters, setFilters] = useState({
    yearOfStudy: '',
    semester: '',
    name: '',
    code: '',
  })
  const resources = [courses, users]
  const failed = resources.find((resource) => resource.error)
  const loading = resources.some((resource) => resource.loading)
  const lecturers = users.data.filter((user) => user.role === 'lecturer' && user.is_active)
  const filteredCourses = courses.data.filter((course) => (
    (!filters.yearOfStudy || String(course.year_of_study || '') === filters.yearOfStudy)
    && (!filters.semester || String(course.semester || '') === filters.semester)
    && (!filters.name || course.name.toLowerCase().includes(filters.name.trim().toLowerCase()))
    && (!filters.code || course.code.toLowerCase().includes(filters.code.trim().toLowerCase()))
  ))

  function refresh() {
    setEditing(null)
    courses.retry()
  }

  return (
    <div className="page-content">
      <PageHeading
        description="Create and update course records using the authenticated Django API."
        eyebrow="Administration"
        title="Course management"
      />
      <AdminCourseForm
        course={editing}
        key={editing?.id || 'new-course'}
        lecturers={lecturers}
        onCancel={() => setEditing(null)}
        onSaved={refresh}
      />
      <div className="course-management-filters">
        <label>Course name<input className="admin-control" onChange={(event) => setFilters({ ...filters, name: event.target.value })} value={filters.name} /></label>
        <label>Course code<input className="admin-control" onChange={(event) => setFilters({ ...filters, code: event.target.value })} value={filters.code} /></label>
        <label>Year of study
          <select className="admin-control" onChange={(event) => setFilters({ ...filters, yearOfStudy: event.target.value })} value={filters.yearOfStudy}>
            <option value="">All years of study</option>
            {YEAR_OF_STUDY_OPTIONS.map((year) => <option key={year} value={year}>Year {year}</option>)}
          </select>
        </label>
        <label>Semester
          <select className="admin-control" onChange={(event) => setFilters({ ...filters, semester: event.target.value })} value={filters.semester}>
            <option value="">All semesters</option>
            {SEMESTER_OPTIONS.map((semester) => <option key={semester} value={semester}>Semester {semester}</option>)}
          </select>
        </label>
      </div>
      <p aria-live="polite" className="result-count">{loading || failed ? 'Loading courses' : `${filteredCourses.length} ${filteredCourses.length === 1 ? 'course' : 'courses'}`}</p>
      {loading || failed
        ? <ApiState
            authRequired={failed?.authRequired}
            error={failed?.error}
            loading={loading}
            retry={() => resources.forEach((resource) => resource.error && resource.retry())}
          />
        : <div className="admin-table-wrap"><table className="admin-table">
            <thead><tr><th scope="col">Course code</th><th scope="col">Course name</th><th scope="col">Year of study</th><th scope="col">Semester</th><th scope="col">Department</th><th scope="col">Lead lecturer</th><th scope="col">Status</th><th scope="col">Actions</th></tr></thead>
            <tbody>
              {filteredCourses.map((course) => {
                const lecturer = lookup(users.data, course.lecturer)
                return <tr key={course.id}>
                  <th scope="row"><a href={`${DJANGO_BASE}/courses/${course.id}/`}>{course.code || '—'}</a></th>
                  <td>{course.name}</td>
                  <td>{course.year_of_study_label || 'Not classified'}</td>
                  <td>{course.semester_label || 'Not classified'}</td>
                  <td>{course.programme || '—'}</td>
                  <td>{lecturer ? [lecturer.first_name, lecturer.last_name].filter(Boolean).join(' ') || lecturer.username : 'Unassigned'}</td>
                  <td><StatusBadge active={course.is_active} /></td>
                  <td><button className="button button-secondary button-small" onClick={() => setEditing(course)} type="button">Edit</button></td>
                </tr>
              })}
              {!filteredCourses.length && <tr><td colSpan="8"><EmptyPanel title={Object.values(filters).some(Boolean) ? 'No matching courses' : 'No courses yet'} description="Create a course above to populate the catalogue." /></td></tr>}
            </tbody>
          </table></div>}
    </div>
  )
}

function ActivityTable({ activities }) {
  return activities.length
    ? <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th scope="col">When</th><th scope="col">User</th><th scope="col">Action</th><th scope="col">Context</th></tr></thead>
        <tbody>{activities.map((activity) => <tr key={activity.id}>
          <td><time dateTime={activity.timestamp}>{formatDate(activity.timestamp)}</time></td>
          <td>{activity.user}</td>
          <td><span className="activity-category">{activity.category.replaceAll('_', ' ')}</span><div>{activity.action}</div></td>
          <td>{[activity.assignment, activity.group].filter(Boolean).join(' · ') || 'System'}</td>
        </tr>)}</tbody>
      </table></div>
    : <EmptyPanel title="No activity recorded" description="Recorded system and administrative actions will appear here." />
}

function AdminActivityPage() {
  const activity = useApiList('admin/activity')
  return (
    <div className="page-content">
      <PageHeading
        action={<a className="button button-secondary" href={`${DJANGO_BASE}/reports/?type=admin_activity`}>Open full activity report</a>}
        description="Recent audit and in-app events. Recorded activity is a system trace, not a measure of academic effort."
        eyebrow="Administration"
        title="System activity"
      />
      {activity.loading || activity.error
        ? <ApiState authRequired={activity.authRequired} error={activity.error} loading={activity.loading} retry={activity.retry} />
        : <ActivityTable activities={activity.data} />}
    </div>
  )
}

function AdminReportsPage() {
  const summary = useApiRecord('admin/summary')
  return (
    <div className="page-content">
      <PageHeading
        description="Administrative summaries are based on live SGAMS records. Detailed downloadable reports use the existing Django reporting workflows."
        eyebrow="Administration"
        title="Administrative reports"
      />
      {summary.loading || summary.error
        ? <ApiState authRequired={summary.authRequired} error={summary.error} loading={summary.loading} retry={summary.retry} />
        : <div className="summary-grid admin-summary-grid">
            <SummaryCard detail="Active accounts" href="/users" label="User accounts" tone="blue" value={summary.data.users.total} />
            <SummaryCard detail="Active courses" href="/courses" label="Courses" tone="teal" value={summary.data.courses.total} />
            <SummaryCard detail="Published assignments" href={`${DJANGO_BASE}/assignments/`} label="Assignments" tone="gold" value={summary.data.assignments.total} />
            <SummaryCard detail={`${summary.data.submissions.latest} latest submissions recorded`} href={`${DJANGO_BASE}/submissions/`} label="Awaiting review" tone="blue" value={summary.data.submissions.awaiting_review} />
          </div>}
      <section className="section-block">
        <div className="section-heading"><div><p className="eyebrow">Detailed reports</p><h2>Download or inspect reports</h2></div></div>
        <div className="admin-action-grid">
          <a className="detail-card admin-action-card" href={`${DJANGO_BASE}/reports/?type=admin_activity&format=csv`}><strong>Administrative activity report</strong><span>Review and export audit and system activity.</span></a>
          <a className="detail-card admin-action-card" href={`${DJANGO_BASE}/reports/?type=course_summary&format=csv`}><strong>Course summary report</strong><span>Course, assignment, group and submission totals.</span></a>
          <a className="detail-card admin-action-card" href={`${DJANGO_BASE}/reports/?type=submission&format=csv`}><strong>Submission report</strong><span>Submission status and deadline overview.</span></a>
        </div>
      </section>
      <p className="scope-note">Full report filters and CSV exports continue to use Django's existing authenticated, administrator-scoped reporting views.</p>
    </div>
  )
}

function NotFoundPage() {
  return (
    <div className="page-content">
      <PageHeading eyebrow="Page not found" title="We couldn't find that page" />
      <Link className="button button-primary" to="/">Return to dashboard</Link>
    </div>
  )
}

function App() {
  return (
    <BrowserRouter>
      <PortalLayout />
    </BrowserRouter>
  )
}

export default App
