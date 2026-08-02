import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

import { authLogin, authMe, authSignup, SESSION_KEY, type AuthUser, type Org } from '../api'

interface AuthState {
  user: AuthUser
  orgs: Org[]
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

/** Access the logged-in user; only valid inside AuthProvider's children. */
export const useAuth = () => {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

/** Gates the app behind email/password login. Renders the login screen until
 * a valid session exists, then the app with the user in context. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [orgs, setOrgs] = useState<Org[]>([])
  const [checking, setChecking] = useState(true)

  // Validate any stored session on load.
  useEffect(() => {
    if (!localStorage.getItem(SESSION_KEY)) {
      setChecking(false)
      return
    }
    authMe()
      .then((r) => {
        setUser(r.user)
        setOrgs(r.orgs)
      })
      .catch(() => localStorage.removeItem(SESSION_KEY))
      .finally(() => setChecking(false))
  }, [])

  const onAuthed = (token: string, u: AuthUser, o: Org[]) => {
    localStorage.setItem(SESSION_KEY, token)
    setUser(u)
    setOrgs(o)
  }

  const logout = () => {
    localStorage.removeItem(SESSION_KEY)
    setUser(null)
    setOrgs([])
  }

  if (checking) return <div className="auth-loading">Loading…</div>
  if (!user) return <LoginScreen onAuthed={onAuthed} />

  return <AuthContext.Provider value={{ user, orgs, logout }}>{children}</AuthContext.Provider>
}

function LoginScreen({
  onAuthed,
}: {
  onAuthed: (token: string, user: AuthUser, orgs: Org[]) => void
}) {
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r =
        mode === 'signup'
          ? await authSignup(email, password, name)
          : await authLogin(email, password)
      onAuthed(r.token, r.user, r.orgs)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand">
          <span className="auth-logo">⚛</span> GenXAI Studio
        </div>
        <h1>{mode === 'signup' ? 'Create your account' : 'Sign in'}</h1>

        {mode === 'signup' && (
          <label className="auth-field">
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
          </label>
        )}
        <label className="auth-field">
          <span>Email</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            autoComplete="email"
          />
        </label>
        <label className="auth-field">
          <span>Password</span>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={mode === 'signup' ? 'At least 8 characters' : 'Your password'}
            autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
          />
        </label>

        {error && <p className="auth-error">{error}</p>}

        <button type="submit" className="auth-submit" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'signup' ? 'Create account' : 'Sign in'}
        </button>

        <p className="auth-switch">
          {mode === 'signup' ? 'Already have an account?' : "Don't have an account?"}{' '}
          <button
            type="button"
            onClick={() => {
              setMode(mode === 'signup' ? 'login' : 'signup')
              setError(null)
            }}
          >
            {mode === 'signup' ? 'Sign in' : 'Sign up'}
          </button>
        </p>
      </form>
    </div>
  )
}
