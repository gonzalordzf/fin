# Finanzas personales — contexto para Claude Code

Sistema de finanzas personales de Gonzalo Rodríguez Fierro (RFC ROFG950407NCA). Este
archivo es la memoria del proyecto: las reglas ya decididas, cómo está armado, y qué
falta. Cualquier sesión debería leer esto antes de tocar código.

> Adaptado de un método/kit compartido (`docs/`), pero la implementación real de este
> repo usa FastAPI + SQLite, no el patrón de JSON planos + HTML estático del kit. Ver
> "Diferencias con el kit compartido" abajo antes de asumir que algo de `docs/` ya
> aplica tal cual.

## Qué es cada cosa

| Ruta | Qué es |
|---|---|
| `backend/app/models.py` | Esquema SQLAlchemy: `Account`, `Category`, `Transaction`, `HoldingSnapshot`, `EquityCompensationEntry`, `AlternativeInvestmentEntry`. Todas las columnas de dinero son `Numeric(p, s, asdecimal=False)` — sin `asdecimal=False` SQLAlchemy regresa `Decimal` y los dedups contra `float` fallan en silencio (ya pasó una vez). |
| `backend/app/parsers/<cuenta>.py` | Un parser por fuente: toma el archivo crudo (PDF/CSV/XML) y regresa dataclasses. No toca la DB. |
| `backend/app/importers/<cuenta>.py` | Envuelve al parser correspondiente, hace dedup, inserta en `Transaction`/`HoldingSnapshot`/etc. Cada uno es idempotente — correrlo dos veces con el mismo archivo no duplica nada. |
| `backend/app/manual_data.py` | Hechos que el usuario dio directamente y que ningún estado de cuenta reporta (ej. el balance real de GBM, sacado de un screenshot de la app). Aplicado en seed, con cita de fuente igual que un parser. |
| `backend/app/classify.py` | Detección de traspasos propios por titular/RFC (no por banco destino — ver gotcha abajo). |
| `backend/app/seed.py` | Siembra las 9 cuentas + categorías + `manual_data`. Re-correr es seguro. |
| `backend/app/main.py` | FastAPI: `POST /import/{account}`, `POST /classify`, `GET /accounts`, `GET /transactions`, `GET /spending-by-category`, `GET /net-worth`. |
| `data/imports/<Cuenta>/` | Carpeta de aterrizaje para estados nuevos, una por institución. Nunca se commitea contenido real (ver `.gitignore`, excluye por extensión). |
| `frontend/` | Vacío todavía — el dashboard no está construido. |

## Mis cuentas

| Cuenta | Institución | Tipo | Moneda | Notas |
|---|---|---|---|---|
| BBVA | BBVA México | transactional | MXN | PDF protegido con contraseña = RFC sin homoclave (`ROFG950407`). El campo `description` del parser solo trae la primera línea del SPEI; el nombre del beneficiario vive en `raw_description` (línea de continuación). |
| AMEX | American Express | transactional | MXN | CSV, `Referencia` es confiable como llave de dedup. |
| Revolut | Revolut | transactional | MXN | PDF, montos vienen en convención invertida (se voltea el signo al importar). |
| Bitso | Bitso | investment_formal | MXN | CSV. |
| GBM | GBM | investment_formal | MXN | Dos sub-contratos (`GBM AAU94801`, `GBM AAU94802`) colgados como hijos vía `parent_account_id`. El Addenda XML solo trae interés diario, nunca el capital invertido — el balance real de la cuenta padre viene de `manual_data.py` (screenshot de la app), y los hijos se excluyen del total para no duplicar (`excluded_from_total`). |
| Balagan | Balagan | investment_informal | MXN | Inversión informal, contrato de colaboración. `INITIAL_INVESTMENT_MXN = 75000.0` es constante (Cláusula TERCERA, no sale de ningún estado). Balance = solo los $75,000 — la "Repartición por punto" mensual se paga en efectivo cada mes, no se queda en la cuenta, así que NO se suma al balance (confirmado con el usuario; antes era un bug real: se sumaba como si se retuviera/compusiera). `detail.cash_distributed_to_date` y `detail.average_monthly_return_pct` reportan esos pagos como métrica de retorno separada. Aún pendiente: si el rescate real del principal es a valor fijo ($75k, Cláusulas QUINTA/SEXTA) o ajustado por crecimiento. |
| Optimax (Allianz) | Allianz | investment_formal | MXN | PDF con 3 sub-portafolios. |
| Shareworks | Shareworks (Coca-Cola) | equity_compensation | USD | PDF. |
| AFORE (Sura) | AFORE SURA | investment_formal | MXN | PDF de 1 página (`detalleMovimientos.pdf`, sin fecha en el nombre — la fecha real sale de "FECHA Y HORA DE EMISIÓN" dentro del PDF). Balance reportado como snapshot único en 4 subcuentas (Retiro, Vivienda, Voluntario, Saldo en tránsito) que suman exacto al "SALDO ACTUAL" impreso. La sección "MOVIMIENTOS EN TU CUENTA" (aportaciones INFONAVIT/patronal/IMSS) NO se parsea a `Transaction` a propósito — ya están incluidas en el saldo, y crear transacciones además del snapshot duplicaría el total (mismo error que se evitó con GBM). |

## Números ancla

*(Sección para llenar cuando exista `/net-worth` corrido con datos reales completos —
no copiar cifras a mano aquí; son las que regresa el endpoint.)*

## Reglas duras del sistema (no se sustituyen sin decisión explícita)

- **El signo del monto es la convención del proyecto**: negativo = sale (cargo/gasto),
  positivo = entra (abono/ingreso). AMEX y Revolut traen el signo invertido en crudo;
  se voltea en el parser, nunca en el importador ni en la DB directamente.
- **Cada `Transaction` se dedupea por `(account_id, source_file, source_row)`**, no por
  fecha+monto+descripción — BBVA repite fecha+monto+descripción sin referencia en
  cargos de comisión el mismo día.
- **Un traspaso se reconoce por TITULAR (mi nombre o mi RFC como destinatario), nunca
  por el banco destino.** `app/classify.py` implementa esto. Ya se confirmó contra
  estados reales que dos personas con apellido Fierro (no soy yo) aparecen como
  remitentes/destinatarios en mis estados — machear solo por apellido habría sido un
  falso positivo real, no hipotético.
- **Dinero institucional (GBM/Bitso/Sura) no se detecta por titular, se detecta por
  institución** — cuando el remitente/destinatario de un SPEI es una de mis propias
  cuentas formales de inversión, el estado muestra el nombre de la institución, no mi
  nombre. Esto es distinto del caso anterior y **todavía no está implementado** —
  ver `docs/02-categorias.md`: probablemente debe ser categoría "Inversión" (Necesario),
  no "Transferencia entre Cuentas Propias". No mezclar los dos mecanismos sin pensarlo.
- **Ningún número se hardcodea si existe una fuente que lo reporte.** Cuando no existe
  ninguna fuente (ej. la aportación inicial de Balagan, el balance real de GBM), se
  documenta como constante o como `manual_data.py`, siempre citando de dónde salió.
- **Los archivos financieros reales nunca se commitean.** `data/imports/**/*.{pdf,xml,csv,zip}`
  está en `.gitignore` por extensión (no por carpeta, para conservar los `.gitkeep`).
- **Las contraseñas nunca se hardcodean.** Viven en `.env` (gitignored); `.env.example`
  documenta el esquema sin el valor real.

## Reglas ya decididas (no re-litigar)

1. GBM: el balance de la cuenta padre es el que reportó el usuario manualmente
   (screenshot 2026-06-18, `manual_data.py`); los sub-contratos AAU94801/AAU94802 se
   muestran en el desglose pero se excluyen del total.
2. Balagan: el balance es SOLO `initial_investment` ($75,000, constante contractual).
   La "Repartición por punto" mensual se paga en efectivo y NUNCA se suma al balance —
   se reporta aparte como `cash_distributed_to_date` / `average_monthly_return_pct`.

## Abierto / sin resolver

- **Balagan, mecánica de rescate**: pregunté si "recuperar la inversión" significa
  exactamente $75,000 (valor original, Cláusulas QUINTA/SEXTA) o un monto ajustado por
  desempeño — sin confirmar todavía. No asumir ninguna de las dos en cálculos de "cuánto
  puedo recuperar".
- **Traspasos institucionales** (BBVA↔GBM/Bitso/Sura): mecanismo de detección pendiente,
  ver arriba.
- **Categorización automática de gasto**: implementada (`app/rules.py`, `app/classify.py`),
  validada contra datos reales de BBVA y AMEX. Cobertura parcial — sigue creciendo con
  cada corte, per el propio principio del kit ("al tercer corte casi todo se clasifica
  solo"). AMEX está excluida del matcher de traspasos por titular/RFC (ver regla dura
  arriba) porque su CSV trae el nombre del dueño de tarjeta en cada renglón.
- **Hard balance validation**: implementada donde existe un total impreso real contra
  el cual cuadrar, verificado con datos reales de cada fuente:
  - BBVA: cargos/abonos + cadena de saldos entre estados.
  - Revolut: Total cargos/Total abonos impresos.
  - Optimax: Monto == Unidades × Valor de la Unidad (tolerancia 5¢ por redondeo real).
  - Balagan: Repartición por punto ≈ PARTICIPATION_PCT × net_income (tolerancia $1).
  - AFORE: subcuentas suman exacto a SALDO ACTUAL.
  - **AMEX, GBM y Bitso NO tienen ningún total impreso contra el cual validar** (CSV
    plano sin fila de resumen / XML del Addenda sin total real / CSV sin línea de
    total) — confirmado revisando la estructura real de cada uno, no es que falte
    implementar, es que no existe qué chequear.
  - **Shareworks**: se intentó (Payroll Credit vs "Cash Value" impreso) y se descartó —
    probado contra un segundo trimestre real, la relación no se sostiene (Cash Value
    probablemente neta compras de acciones ESPP no extraídas). Un docstring anterior
    afirmaba haberlo "verificado" con solo un trimestre; ya se corrigió esa afirmación
    falsa. No re-agregar sin antes extraer también las transacciones "Buy".

## Diferencias con el kit compartido (`docs/`)

El método en `docs/` asume un libro mayor en JSON plano (`data/transacciones.json`) y
un dashboard HTML de un solo archivo generado por `build.py`/`ux.py`. Este proyecto usa
SQLite + SQLAlchemy + FastAPI en su lugar — la arquitectura de archivos de `docs/` no
aplica literalmente. Lo que sí se adoptó del kit: el principio de detectar traspasos por
titular (no por banco), la idea de una capa "Básico/Necesario/Estilo de vida" sobre las
categorías, y el catálogo de widgets (`docs/03-widgets.md`) como referencia para cuando
se construya el dashboard — pendiente decidir si el dashboard sigue siendo un HTML
estático servido por esta API o el patrón de archivo único del kit.

## Cómo correr

```bash
cd backend
cp ../.env.example ../.env   # llenar BBVA_STATEMENT_PASSWORD
pip install -r requirements.txt
python3 -m app.seed
uvicorn app.main:app --reload
```

Ciclo de importación: dejar el estado nuevo en `data/imports/<Cuenta>/`, luego
`POST /import/{account}`, luego `POST /classify`.

## Tono

Directo y técnico, sin jerga innecesaria. Cero moralina, cero lenguaje de coach. Los
textos y el análisis financiero son descriptivos y precisos, no motivacionales.
