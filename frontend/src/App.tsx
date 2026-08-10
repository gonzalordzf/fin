import { useEffect, useMemo, useState } from 'react'
import './App.css'
import { fetchMonthlySummary, fetchNetWorth } from './api'
import type { MonthSummary, NetWorth } from './api'
import { StatTile } from './components/StatTile'
import { CashFlowChart } from './components/CashFlowChart'
import { CategoryBreakdown } from './components/CategoryBreakdown'

export default function App() {
  const [currency, setCurrency] = useState('MXN')
  const [months, setMonths] = useState<MonthSummary[] | null>(null)
  const [netWorth, setNetWorth] = useState<NetWorth | null>(null)
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchNetWorth().then(setNetWorth).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    setMonths(null)
    fetchMonthlySummary(currency)
      .then((data) => {
        setMonths(data)
        setSelectedMonth((prev) => {
          if (prev && data.some((m) => m.month === prev)) return prev
          return data.length ? data[data.length - 1].month : null
        })
      })
      .catch((e) => setError(String(e)))
  }, [currency])

  const currentMonth = useMemo(
    () => months?.find((m) => m.month === selectedMonth) ?? null,
    [months, selectedMonth],
  )

  const currencies = useMemo(() => {
    const set = new Set<string>(['MXN'])
    if (netWorth) {
      for (const c of Object.keys(netWorth.total_by_currency)) set.add(c)
    }
    return Array.from(set)
  }, [netWorth])

  if (error) {
    return (
      <div className="app">
        <p className="app__error">No se pudo cargar el dashboard: {error}</p>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1 className="app__title">Finanzas — Gonzalo Rodríguez Fierro</h1>
          <p className="app__subtitle">Análisis mes a mes de ingreso, gasto y patrimonio</p>
        </div>
        <select
          className="app__currency-select"
          value={currency}
          onChange={(e) => setCurrency(e.target.value)}
        >
          {currencies.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </header>

      {netWorth && (
        <section className="app__section">
          <div className="app__stat-row">
            {Object.entries(netWorth.total_by_currency).map(([cur, total]) => (
              <StatTile key={cur} label={`Patrimonio (${cur})`} value={total} currency={cur} />
            ))}
            {currentMonth && (
              <>
                <StatTile
                  label={`Ingreso — ${currentMonth.month}`}
                  value={currentMonth.income}
                  currency={currency}
                />
                <StatTile
                  label={`Gasto — ${currentMonth.month}`}
                  value={currentMonth.total_expense}
                  currency={currency}
                />
                <StatTile
                  label={`Neto — ${currentMonth.month}`}
                  value={currentMonth.net}
                  currency={currency}
                />
              </>
            )}
          </div>
          {netWorth.caveats.length > 0 && (
            <details className="app__caveats">
              <summary>{netWorth.caveats.length} nota(s) sobre el cálculo de patrimonio</summary>
              <ul>
                {netWorth.caveats.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}

      <section className="app__section">
        {months === null ? (
          <p className="app__loading">Cargando…</p>
        ) : months.length === 0 ? (
          <p className="app__loading">Sin transacciones en {currency} todavía.</p>
        ) : (
          <CashFlowChart
            months={months}
            currency={currency}
            selectedMonth={selectedMonth}
            onSelectMonth={setSelectedMonth}
          />
        )}
      </section>

      <section className="app__section">
        <CategoryBreakdown month={currentMonth} currency={currency} />
      </section>
    </div>
  )
}
