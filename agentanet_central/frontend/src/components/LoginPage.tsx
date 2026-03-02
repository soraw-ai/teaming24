import { useState } from 'react'
import { useAuthStore } from '../store/authStore'

export default function LoginPage() {
  const { login, isLoading, error, clearError } = useAuthStore()
  const [username, setUsername] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (username.trim()) {
      await login(username.trim())
    }
  }

  const quickLogin = (name: string) => {
    setUsername(name)
    login(name)
  }

  return (
    <div className="min-h-screen bg-dark-bg flex items-center justify-center p-4">
      {/* Background decoration */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-40 -right-40 w-80 h-80 bg-primary-500/10 rounded-full blur-3xl" />
        <div className="absolute -bottom-40 -left-40 w-80 h-80 bg-purple-500/10 rounded-full blur-3xl" />
      </div>

      <div className="w-full max-w-md relative animate-fade-in">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-2xl bg-gradient-to-br from-primary-500 to-purple-600 mb-4 glow">
            <span className="text-4xl font-bold text-white">A</span>
          </div>
          <h1 className="text-3xl font-bold text-white">AgentaNet Central</h1>
          <p className="text-gray-400 mt-2">Manage your agentic nodes</p>
        </div>

        {/* Login Card */}
        <div className="bg-dark-surface rounded-2xl border border-dark-border p-8 card-hover">
          <h2 className="text-xl font-semibold text-white mb-6">Sign In</h2>
          
          {/* Mock GitHub Login Notice */}
          <div className="mb-6 p-4 bg-gradient-to-r from-yellow-500/10 to-orange-500/10 border border-yellow-500/20 rounded-xl">
            <p className="text-sm font-medium text-yellow-400">
              Development Mode
            </p>
            <p className="text-xs text-yellow-400/70 mt-1">
              Mock GitHub OAuth - Select a user below
            </p>
          </div>

          {/* Quick Login Buttons */}
          <div className="grid grid-cols-2 gap-3 mb-6">
            <button
              onClick={() => quickLogin('admin')}
              disabled={isLoading}
              className="px-4 py-3 bg-orange-500/10 hover:bg-orange-500/20 border border-orange-500/30 
                       text-orange-400 rounded-xl transition-all btn-press flex items-center justify-center gap-2"
            >
              <span className="w-6 h-6 rounded-full bg-orange-500/20 flex items-center justify-center text-xs">A</span>
              admin
            </button>
            <button
              onClick={() => quickLogin('demo')}
              disabled={isLoading}
              className="px-4 py-3 bg-primary-500/10 hover:bg-primary-500/20 border border-primary-500/30 
                       text-primary-400 rounded-xl transition-all btn-press flex items-center justify-center gap-2"
            >
              <span className="w-6 h-6 rounded-full bg-primary-500/20 flex items-center justify-center text-xs">D</span>
              demo
            </button>
            <button
              onClick={() => quickLogin('alice')}
              disabled={isLoading}
              className="px-4 py-3 bg-green-500/10 hover:bg-green-500/20 border border-green-500/30 
                       text-green-400 rounded-xl transition-all btn-press flex items-center justify-center gap-2"
            >
              <span className="w-6 h-6 rounded-full bg-green-500/20 flex items-center justify-center text-xs">A</span>
              alice
            </button>
            <button
              onClick={() => quickLogin('bob')}
              disabled={isLoading}
              className="px-4 py-3 bg-blue-500/10 hover:bg-blue-500/20 border border-blue-500/30 
                       text-blue-400 rounded-xl transition-all btn-press flex items-center justify-center gap-2"
            >
              <span className="w-6 h-6 rounded-full bg-blue-500/20 flex items-center justify-center text-xs">B</span>
              bob
            </button>
          </div>

          <div className="relative mb-6">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-dark-border"></div>
            </div>
            <div className="relative flex justify-center text-xs">
              <span className="px-2 bg-dark-surface text-gray-500">or enter username</span>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <input
                type="text"
                value={username}
                onChange={(e) => {
                  setUsername(e.target.value)
                  clearError()
                }}
                placeholder="GitHub username"
                className="w-full px-4 py-3 bg-dark-bg border border-dark-border rounded-xl
                         text-white placeholder-gray-500 focus:outline-none focus:border-primary-500
                         transition-colors"
              />
            </div>

            {error && (
              <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-xl animate-scale-in">
                <p className="text-sm text-red-400">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={isLoading || !username.trim()}
              className="w-full py-3 bg-gradient-to-r from-primary-600 to-purple-600 hover:from-primary-500 hover:to-purple-500
                       text-white font-medium rounded-xl transition-all disabled:opacity-50 disabled:cursor-not-allowed
                       flex items-center justify-center gap-2 btn-press"
            >
              {isLoading ? (
                <span className="flex items-center gap-2">
                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  Signing in...
                </span>
              ) : (
                <>
                  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                  </svg>
                  Sign in with GitHub
                </>
              )}
            </button>
          </form>
        </div>

        <p className="text-center text-gray-500 text-xs mt-6">
          AgentaNet Central Service v0.1.0
        </p>
      </div>
    </div>
  )
}
