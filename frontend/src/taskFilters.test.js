import assert from 'node:assert/strict'
import test from 'node:test'

import { filterTasks } from './taskFilters.js'

const now = new Date('2026-11-01T10:00:00.000Z')
const tasks = [
  {
    id: 1,
    assigned_to: 7,
    group: 10,
    status: 'todo',
    deadline: '2026-11-03T10:00:00.000Z',
  },
  {
    id: 2,
    assigned_to: 8,
    group: 10,
    status: 'in_progress',
    deadline: '2026-11-08T10:00:00.000Z',
  },
  {
    id: 3,
    assigned_to: 8,
    group: 11,
    status: 'todo',
    deadline: '2026-11-10T10:00:00.000Z',
  },
  {
    id: 4,
    assigned_to: 7,
    group: 10,
    status: 'completed',
    deadline: '2026-11-02T10:00:00.000Z',
  },
  {
    id: 5,
    assigned_to: 7,
    group: 10,
    status: 'todo',
    deadline: '2026-10-31T10:00:00.000Z',
  },
  {
    id: 6,
    assigned_to: 7,
    group: 10,
    status: 'todo',
    deadline: null,
  },
]

test('group scope includes own tasks and tasks assigned to other group members', () => {
  const visible = filterTasks(tasks, {
    scope: 'group',
    userId: 7,
    statusFilter: 'all',
    groupFilter: 10,
    now,
  })

  assert.deepEqual(visible.map((task) => task.id), [1, 2, 4, 5, 6])
})

test('my scope includes only tasks assigned to the signed-in user', () => {
  const visible = filterTasks(tasks, {
    scope: 'mine',
    userId: 7,
    statusFilter: 'all',
    groupFilter: null,
    now,
  })

  assert.deepEqual(visible.map((task) => task.id), [1, 4, 5, 6])
})

test('upcoming scope includes only incomplete deadlines within the next seven days', () => {
  const visible = filterTasks(tasks, {
    scope: 'group',
    userId: 7,
    statusFilter: 'upcoming',
    groupFilter: null,
    now,
  })

  assert.deepEqual(visible.map((task) => task.id), [1, 2])
})
