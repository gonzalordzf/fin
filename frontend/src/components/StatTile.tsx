import './StatTile.css'

function formatMoney(value: number, currency: string): string {
  return new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(value)
}

export function StatTile({
  label,
  value,
  currency,
}: {
  label: string
  value: number
  currency: string
}) {
  return (
    <div className="stat-tile">
      <p className="stat-tile__label">{label}</p>
      <p className={`stat-tile__value${value < 0 ? ' stat-tile__value--negative' : ''}`}>
        {formatMoney(value, currency)}
      </p>
    </div>
  )
}
