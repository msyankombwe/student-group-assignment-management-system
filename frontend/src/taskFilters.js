const UPCOMING_WINDOW_MS = 7 * 24 * 60 * 60 * 1000

export function filterTasks(tasks, {
  scope,
  userId,
  statusFilter,
  groupFilter,
  now = new Date(),
}) {
  const nowMs = now.getTime()
  const upcomingCutoff = nowMs + UPCOMING_WINDOW_MS

  return tasks.filter((task) => {
    const belongsToScope = scope !== 'mine' || task.assigned_to === userId
    const status = task.effective_status || task.status
    const deadline = task.deadline ? Date.parse(task.deadline) : Number.NaN
    const matchesStatus = statusFilter === 'all'
      || (statusFilter === 'pending' && status !== 'completed')
      || (
        statusFilter === 'upcoming'
        && status !== 'completed'
        && status !== 'overdue'
        && Number.isFinite(deadline)
        && deadline >= nowMs
        && deadline <= upcomingCutoff
      )
      || status === statusFilter

    return belongsToScope
      && matchesStatus
      && (!groupFilter || task.group === groupFilter)
  })
}
