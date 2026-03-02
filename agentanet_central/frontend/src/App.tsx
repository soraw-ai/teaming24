import { useEffect, useState } from 'react'
import { useAuthStore } from './store/authStore'
import LoginPage from './components/LoginPage'
import Dashboard from './components/Dashboard'
import AdminDashboard from './components/AdminDashboard'

export default function App() {
  const { user, fetchUser } = useAuthStore()
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchUser().finally(() => setLoading(false))
  }, [fetchUser])

  if (loading) {
    return (
      <div className="min-h-screen bg-dark-bg flex items-center justify-center">
        <div className="animate-pulse flex flex-col items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
            <span className="text-2xl font-bold text-white">A</span>
          </div>
          <div className="text-gray-400">Loading...</div>
        </div>
      </div>
    )
  }

  if (!user) {
    return <LoginPage />
  }

  return user.is_admin ? <AdminDashboard /> : <Dashboard />
}
