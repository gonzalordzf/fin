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

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`)
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export function fetchMonthlySummary(currency: string): Promise<MonthSummary[]> {
  return getJSON(`/monthly-summary?currency=${encodeURIComponent(currency)}`)
}

export function fetchNetWorth(): Promise<NetWorth> {
  return getJSON('/net-worth')
}
