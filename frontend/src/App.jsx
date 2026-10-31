import React from 'react'
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom'
import axios from 'axios'
import './App.css'

const API_BASE = 'http://localhost:8000/api'

function UserList() {
  const [users, setUsers] = React.useState([])

  React.useEffect(() => {
    axios.get(`${API_BASE}/users/`)
      .then((res) => setUsers(res.data))
      .catch((err) => console.error(err))
  }, [])

  return (
    <div className="card">
      <h2>Users</h2>
      <ul>
        {users.map((user) => (
          <li key={user.id}>{user.username} - {user.role}</li>
        ))}
      </ul>
    </div>
  )
}

function CourseList() {
  const [courses, setCourses] = React.useState([])

  React.useEffect(() => {
    axios.get(`${API_BASE}/courses/`)
      .then((res) => setCourses(res.data))
      .catch((err) => console.error(err))
  }, [])

  return (
    <div className="card">
      <h2>Courses</h2>
      <ul>
        {courses.map((course) => (
          <li key={course.id}>{course.name} - {course.semester}</li>
        ))}
      </ul>
    </div>
  )
}

function App() {
  return (
    <Router>
      <div className="page">
        <nav className="nav">
          <Link to="/users">Users</Link>
          <Link to="/courses">Courses</Link>
        </nav>

        <Routes>
          <Route path="/users" element={<UserList />} />
          <Route path="/courses" element={<CourseList />} />
          <Route path="*" element={<UserList />} />
        </Routes>
      </div>
    </Router>
  )
}

export default App
