export interface MonthCategory {
  category: string
  nature: 'basico' | 'necesario' | 'estilo_de_vida' | null
  amount: number
}

export interface MonthSummary {
  month: string // "YYYY-MM"
  income: number
  categories: MonthCategory[]
  uncategorized_expense: number
  total_expense: number
  net: number
}

export interface NetWorthAccount {
  account: string
  kind: string
  balance: number
  currency: string
  detail?: Record<string, number | null>
  excluded_from_total?: boolean
}

export interface NetWorth {
  total_by_currency: Record<string, number>
  by_account: NetWorthAccount[]
  caveats: string[]
}

export class UnauthorizedError extends Error {}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`)
  if (res.status === 401) {
    throw new UnauthorizedError('no autenticado')
  }
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export async function fetchAuthStatus(): Promise<{ authenticated: boolean; configured: boolean }> {
  const res = await fetch('/api/auth/status')
  if (!res.ok) {
    // If auth isn't configured at all (local dev, no APP_PASSWORD set),
    // there's no /auth/status route to hit — treat that as "no gate,
    // proceed" rather than surfacing an error the user can't act on.
    return { authenticated: true, configured: false }
  }
  const body = await res.json()
  return { ...body, configured: true }
}

export async function login(password: string): Promise<void> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: 'Error al iniciar sesión' }))
    throw new Error(body.detail || 'Error al iniciar sesión')
  }
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' })
}

export function fetchMonthlySummary(currency: string): Promise<MonthSummary[]> {
  return getJSON(`/monthly-summary?currency=${encodeURIComponent(currency)}`)
}

export function fetchNetWorth(): Promise<NetWorth> {
  return getJSON('/net-worth')
}
