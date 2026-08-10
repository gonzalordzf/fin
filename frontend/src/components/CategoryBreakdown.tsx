import { useMemo, useState } from 'react'
import type { MonthSummary } from '../api'
import './CategoryBreakdown.css'

const NATURE_LABEL: Record<string, string> = {
  basico: 'Básico',
  necesario: 'Necesario',
  estilo_de_vida: 'Estilo de vida',
}

const NATURE_COLOR: Record<string, string> = {
  basico: 'var(--series-1)',
  necesario: 'var(--series-4)',
  estilo_de_vida: 'var(--series-2)',
}

function formatMoney(value: number, currency: string): string {
  return new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(value)
}

export function CategoryBreakdown({
  month,
  currency,
}: {
  month: MonthSummary | null
  currency: string
}) {
  const [asTable, setAsTable] = useState(false)

  const rows = useMemo(() => {
    if (!month) return []
    const all = [
      ...month.categories.map((c) => ({
        category: c.category,
        nature: c.nature,
        amount: c.amount,
      })),
    ]
    if (month.uncategorized_expense) {
      all.push({ category: 'Sin categoría', nature: null, amount: month.uncategorized_expense })
    }
    return all.sort((a, b) => b.amount - a.amount)
  }, [month])

  const maxAmount = rows.reduce((m, r) => Math.max(m, r.amount), 0) || 1

  if (!month) {
    return (
      <div className="breakdown">
        <p className="breakdown__empty">Selecciona un mes en la gráfica de arriba.</p>
      </div>
    )
  }

  return (
    <div className="breakdown">
      <div className="breakdown__header">
        <p className="breakdown__title">Gasto por categoría — {month.month}</p>
        <div className="breakdown__legend">
          <span className="breakdown__legend-item">
            <span className="breakdown__swatch" style={{ background: 'var(--series-1)' }} />
            Básico
          </span>
          <span className="breakdown__legend-item">
            <span className="breakdown__swatch" style={{ background: 'var(--series-4)' }} />
            Necesario
          </span>
          <span className="breakdown__legend-item">
            <span className="breakdown__swatch" style={{ background: 'var(--series-2)' }} />
            Estilo de vida
          </span>
          <span className="breakdown__legend-item">
            <span className="breakdown__swatch" style={{ background: 'var(--text-muted)' }} />
            Sin categoría
          </span>
        </div>
        <button className="breakdown__toggle" onClick={() => setAsTable((v) => !v)}>
          {asTable ? 'Ver gráfica' : 'Ver tabla'}
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="breakdown__empty">Sin gasto categorizado este mes.</p>
      ) : asTable ? (
        <table className="breakdown__table">
          <thead>
            <tr>
              <th>Categoría</th>
              <th>Naturaleza</th>
              <th>Monto</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.category}>
                <td>{r.category}</td>
                <td>{r.nature ? NATURE_LABEL[r.nature] : '—'}</td>
                <td>{formatMoney(r.amount, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div>
          {rows.map((r) => (
            <div className="breakdown__row" key={r.category}>
              <span className="breakdown__row-label">{r.category}</span>
              <span className="breakdown__row-track">
                <span
                  className="breakdown__row-fill"
                  style={{
                    width: `${Math.max((r.amount / maxAmount) * 100, 1)}%`,
                    background: r.nature ? NATURE_COLOR[r.nature] : 'var(--text-muted)',
                  }}
                />
              </span>
              <span className="breakdown__row-value">{formatMoney(r.amount, currency)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
