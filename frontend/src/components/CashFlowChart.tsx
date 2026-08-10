import { useMemo, useState } from 'react'
import type { MonthSummary } from '../api'
import './CashFlowChart.css'

const CHART_HEIGHT = 294
const PLOT_TOP = 30
const PLOT_BOTTOM = CHART_HEIGHT - 40
const GROUP_WIDTH = 46
const BAR_WIDTH = 16
const BAR_GAP = 4

function formatCompact(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(0)}k`
  return value.toFixed(0)
}

// Gasto/ingreso ratio for a month — undefined when income is 0. Capped
// display at ±999% so a real outlier month (income that dropped to near
// nothing) doesn't blow out the label's width and crowd its neighbors;
// the color still reads over/under 100% at a glance.
function monthRatio(income: number, totalExpense: number): { text: string; color: string } {
  if (!(income > 0)) return { text: '—', color: 'var(--text-muted)' }
  const raw = (totalExpense / income) * 100
  const text = Math.abs(raw) > 999 ? `${raw > 0 ? '999' : '-999'}%+` : `${Math.round(raw)}%`
  return { text, color: raw <= 100 ? 'var(--good)' : 'var(--series-8)' }
}

function formatMoney(value: number, currency: string): string {
  return new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(value)
}

export function CashFlowChart({
  months,
  currency,
  selectedMonth,
  onSelectMonth,
}: {
  months: MonthSummary[]
  currency: string
  selectedMonth: string | null
  onSelectMonth: (month: string) => void
}) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  // Two-sided domain: income/expense are usually >= 0 but net regularly
  // isn't, and a single-sided [0, maxAbs] scale left no room below the
  // baseline for a deeply negative month — that month's point landed
  // below the plotted area entirely (e.g. -199k net against a 492k max
  // drawn from a *different* month's expense put the dot ~50px off-canvas).
  const [domainMin, domainMax] = useMemo(() => {
    let lo = 0
    let hi = 0
    for (const row of months) {
      lo = Math.min(lo, row.income, row.total_expense, row.net)
      hi = Math.max(hi, row.income, row.total_expense, row.net)
    }
    if (lo === hi) hi = lo + 1
    return [lo, hi]
  }, [months])

  const plotHeight = PLOT_BOTTOM - PLOT_TOP
  const scaleY = (v: number) => PLOT_BOTTOM - ((v - domainMin) / (domainMax - domainMin)) * plotHeight
  // Bars are diverging from the zero baseline: a category can net negative
  // in a heavy-reimbursement month (e.g. a large travel refund), so
  // income/expense aren't guaranteed >= 0 — only "y going up from 0" is.
  const barRect = (v: number) => {
    const y0 = scaleY(0)
    const y1 = scaleY(v)
    return { y: Math.min(y0, y1), height: Math.abs(y1 - y0) }
  }

  const width = Math.max(months.length * GROUP_WIDTH + 40, 480)

  const netPoints = months
    .map((row, i) => {
      const cx = i * GROUP_WIDTH + GROUP_WIDTH / 2
      const cy = scaleY(row.net)
      return `${cx},${cy}`
    })
    .join(' ')

  const gridValues = [0, 0.25, 0.5, 0.75, 1].map((t) => domainMin + (domainMax - domainMin) * t)

  const hovered = hoverIdx !== null ? months[hoverIdx] : null

  return (
    <div className="cashflow">
      <div className="cashflow__header">
        <p className="cashflow__title">Flujo mensual ({currency})</p>
        <div className="cashflow__legend">
          <span className="cashflow__legend-item">
            <span className="cashflow__swatch" style={{ background: 'var(--series-1)' }} />
            Ingreso
          </span>
          <span className="cashflow__legend-item">
            <span className="cashflow__swatch" style={{ background: 'var(--series-2)' }} />
            Gasto
          </span>
          <span className="cashflow__legend-item">
            <span className="cashflow__swatch" style={{ background: 'var(--series-3)' }} />
            Neto
          </span>
        </div>
      </div>
      <div className="cashflow__scroll" style={{ position: 'relative' }}>
        <svg width={width} height={CHART_HEIGHT} role="img" aria-label="Flujo de efectivo mensual">
          {gridValues.map((v) => (
            <g key={v}>
              <line
                className="cashflow__gridline"
                x1={0}
                x2={width}
                y1={scaleY(v)}
                y2={scaleY(v)}
              />
              <text className="cashflow__axis-label" x={4} y={scaleY(v) - 4}>
                {formatCompact(v)}
              </text>
            </g>
          ))}
          <line className="cashflow__baseline" x1={0} x2={width} y1={scaleY(0)} y2={scaleY(0)} />

          {months.map((row, i) => {
            const gx = i * GROUP_WIDTH
            const isSelected = row.month === selectedMonth
            return (
              <g
                key={row.month}
                onMouseEnter={() => setHoverIdx(i)}
                onMouseLeave={() => setHoverIdx(null)}
                onClick={() => onSelectMonth(row.month)}
                className="cashflow__bar"
              >
                <rect
                  x={gx + GROUP_WIDTH / 2 - BAR_WIDTH - BAR_GAP / 2}
                  y={barRect(row.income).y}
                  width={BAR_WIDTH}
                  height={barRect(row.income).height}
                  rx={3}
                  fill="var(--series-1)"
                />
                <rect
                  x={gx + GROUP_WIDTH / 2 + BAR_GAP / 2}
                  y={barRect(row.total_expense).y}
                  width={BAR_WIDTH}
                  height={barRect(row.total_expense).height}
                  rx={3}
                  fill="var(--series-2)"
                />
                <rect
                  x={gx}
                  y={PLOT_TOP}
                  width={GROUP_WIDTH}
                  height={PLOT_BOTTOM - PLOT_TOP}
                  fill="transparent"
                />
                <text
                  className={`cashflow__month-label${isSelected ? ' cashflow__month-label--selected' : ''}`}
                  x={gx + GROUP_WIDTH / 2}
                  y={PLOT_BOTTOM + 16}
                  textAnchor="middle"
                >
                  {row.month.slice(2)}
                </text>
                <text
                  className="cashflow__ratio-label"
                  x={gx + GROUP_WIDTH / 2}
                  y={PLOT_TOP - 14}
                  textAnchor="middle"
                  fill={monthRatio(row.income, row.total_expense).color}
                >
                  {monthRatio(row.income, row.total_expense).text}
                </text>
              </g>
            )
          })}

          <polyline className="cashflow__net-line" points={netPoints} />
          {months.map((row, i) => (
            <circle
              key={row.month}
              className="cashflow__net-dot"
              cx={i * GROUP_WIDTH + GROUP_WIDTH / 2}
              cy={scaleY(row.net)}
              r={hoverIdx === i ? 4 : 2.5}
            />
          ))}

          {hoverIdx !== null && (
            <line
              x1={hoverIdx * GROUP_WIDTH + GROUP_WIDTH / 2}
              x2={hoverIdx * GROUP_WIDTH + GROUP_WIDTH / 2}
              y1={PLOT_TOP}
              y2={PLOT_BOTTOM}
              stroke="var(--axis)"
              strokeDasharray="3,3"
            />
          )}
        </svg>

        {hovered && hoverIdx !== null && (
          <div
            className="cashflow__tooltip"
            style={{
              left: Math.min(hoverIdx * GROUP_WIDTH + GROUP_WIDTH + 8, width - 160),
              top: 8,
            }}
          >
            <div className="cashflow__tooltip-title">{hovered.month}</div>
            <div className="cashflow__tooltip-row">
              <span>Ingreso</span>
              <span>{formatMoney(hovered.income, currency)}</span>
            </div>
            <div className="cashflow__tooltip-row">
              <span>Gasto</span>
              <span>{formatMoney(hovered.total_expense, currency)}</span>
            </div>
            <div className="cashflow__tooltip-row">
              <span>Neto</span>
              <span>{formatMoney(hovered.net, currency)}</span>
            </div>
            <div className="cashflow__tooltip-row">
              <span>Gasto/Ingreso</span>
              <span>{monthRatio(hovered.income, hovered.total_expense).text}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
