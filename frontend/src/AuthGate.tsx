import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import './AuthGate.css'
import { fetchAuthStatus, login } from './api'

export function AuthGate({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    fetchAuthStatus().then((s) => setAuthenticated(s.authenticated))
  }, [])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(password)
      setAuthenticated(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error al iniciar sesión')
    } finally {
      setSubmitting(false)
    }
  }

  if (authenticated === null) {
    return <div className="auth-gate__loading" />
  }

  if (!authenticated) {
    return (
      <div className="auth-gate">
        <form className="auth-gate__form" onSubmit={handleSubmit}>
          <h1 className="auth-gate__title">Finanzas</h1>
          <p className="auth-gate__subtitle">Contraseña requerida</p>
          <input
            className="auth-gate__input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="current-password"
          />
          {error && <p className="auth-gate__error">{error}</p>}
          <button className="auth-gate__submit" type="submit" disabled={submitting}>
            {submitting ? 'Verificando…' : 'Entrar'}
          </button>
        </form>
      </div>
    )
  }

  return <>{children}</>
}
