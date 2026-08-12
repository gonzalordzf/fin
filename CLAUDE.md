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
| `backend/app/main.py` | FastAPI: `POST /import/{account}`, `POST /classify`, `GET /accounts`, `GET /transactions`, `GET /spending-by-category`, `GET /net-worth`, `GET /monthly-summary`, `GET /savings-goal`, `GET /savings-projection`. |
| `backend/app/auth.py` | Gate de password de un solo usuario (sesión firmada vía `SessionMiddleware`/`itsdangerous`), fail-closed: bloquea toda ruta salvo `/auth/login` y `/auth/status`. Solo se activa (`install_auth`, llamado desde `main.py`) si `APP_PASSWORD` está seteado — en dev local nunca lo está, así que el dev local no cambia en nada. Requiere también `SESSION_SECRET` (falla el arranque si falta). Ver `DEPLOY.md`. |
| `data/imports/<Cuenta>/` | Carpeta de aterrizaje para estados nuevos, una por institución. Nunca se commitea contenido real (ver `.gitignore`, excluye por extensión). |
| `frontend/` | Dashboard real: React + Vite + TypeScript, consulta la API en vivo (proxy `/api` → `uvicorn` puerto 8000 vía `vite.config.ts` en dev; en producción, `frontend/functions/api/[[path]].ts` hace de proxy same-origin hacia el backend en Fly.io). Vista principal mes a mes: flujo de efectivo (ingreso/gasto/neto) y desglose de gasto por categoría del mes seleccionado, coloreado por naturaleza (Básico/Necesario/Estilo de vida). Paleta y specs de gráficas siguiendo el skill `dataviz` (`frontend/src/theme.css`), con soporte de modo oscuro y vista de tabla accesible como respaldo de cada gráfica. `AuthGate.tsx` envuelve `<App/>` en `main.tsx`: pantalla de login si el backend tiene auth activo, passthrough si no. |
| `DEPLOY.md` | Cómo desplegar: Cloudflare Pages (frontend) + Fly.io (backend FastAPI/SQLite en volumen persistente). Comandos exactos de `flyctl`/Cloudflare — requieren las cuentas del usuario, no se pueden correr desde aquí. |

## Mis cuentas

| Cuenta | Institución | Tipo | Moneda | Notas |
|---|---|---|---|---|
| BBVA | BBVA México | transactional | MXN | PDF protegido con contraseña = RFC sin homoclave (`ROFG950407`). El campo `description` del parser solo trae la primera línea del SPEI; el nombre del beneficiario vive en `raw_description` (línea de continuación). Solo débito — no incluye la tarjeta de crédito (ver `BBVA TDC` abajo). |
| BBVA TDC | BBVA México | transactional (`is_credit_card=True`) | MXN | Tarjeta de crédito, cuenta separada de `BBVA` (débito). Dos plantillas de estado de cuenta para el mismo producto ("Tarjeta ORO BBVA" — el nombre del producto no cambió, solo el layout): `parsers/bbva_credit.py` para jul-2024 en adelante ("nuevo estado de cuenta universal") y `parsers/bbva_credit_legacy.py` para ene-2023 a jun-2024 (columnas CARGOS/ABONOS separadas por posición x, sin token de signo). `importers/bbva_credit.py` detecta el formato solo (`_detect_format`, por el header de la tabla de movimientos — el texto "Tarjeta ORO BBVA" aparece en ambas plantillas y no sirve para distinguirlas). Sin contraseña, a diferencia de `BBVA` débito. Corte día 4, pago 20 días naturales después (confirmado contra estados reales). El estado de noviembre 2023 se creyó perdido (los archivos "Noviembre 2023"/"Diciembre 2023" en el Drive conectado eran el mismo PDF de diciembre, md5-confirmado) pero el usuario lo encontró y subió después (2026-08-12) — reconcilia exacto contra ambos lados del hueco, sin necesitar el ajuste sintético que se había usado mientras tanto (ver `manual_data.py`'s `MANUAL_TRANSACTIONS`, vacío pero vivo para huecos futuros). Balance ancla en `manual_data.py`: $610.08 al 05-dic-2022 (el "Saldo Inicial del Periodo" del primer estado disponible, ene-2023) — todo lo anterior a esa fecha es desconocido, no hay estado que lo reporte. |
| AMEX | American Express | transactional (`is_credit_card=True`) | MXN | CSV, `Referencia` es confiable como llave de dedup. Corte día 3, pago 15 días hábiles después. |
| Revolut | Revolut | transactional | MXN | PDF, montos vienen en convención invertida (se voltea el signo al importar). |
| Bitso | Bitso | investment_formal | MXN | CSV. |
| GBM | GBM | investment_formal | MXN | Dos sub-contratos (`GBM AAU94801`, `GBM AAU94802`) colgados como hijos vía `parent_account_id`. El Addenda XML solo trae interés diario, nunca el capital invertido — el balance real de la cuenta padre viene de `manual_data.py` (screenshot de la app), y los hijos se excluyen del total para no duplicar (`excluded_from_total`). |
| Balagan | Balagan | investment_informal | MXN | Inversión informal, contrato de colaboración. `INITIAL_INVESTMENT_MXN = 75000.0` es constante (Cláusula TERCERA, no sale de ningún estado de resultados), pero sí se identificó la transferencia real que la fondeó: BBVA SPEI ENVIADO BANORTE, 02-dic-2024, $75,000, memo "inversion Gonzalo", beneficiario "RIVER SA DE CV" — confirmado por el usuario como la razón social de Balagan (tageado como "Inversión" en `app/rules.py` por referencia SPEI). Balance = solo los $75,000 — la "Repartición por punto" mensual se paga en efectivo cada mes, no se queda en la cuenta, así que NO se suma al balance (confirmado con el usuario; antes era un bug real: se sumaba como si se retuviera/compusiera). `detail.cash_distributed_to_date` y `detail.average_monthly_return_pct` reportan esos pagos como métrica de retorno separada. Aún pendiente: si el rescate real del principal es a valor fijo ($75k, Cláusulas QUINTA/SEXTA) o ajustado por crecimiento. |
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
- **Dinero institucional (GBM/Bitso) no se detecta por titular, se detecta por
  institución** — cuando el remitente/destinatario de un SPEI es una de mis propias
  cuentas formales de inversión, el estado muestra el nombre de la institución, no mi
  nombre (a veces sí muestra mi nombre para el mismo tipo de traspaso GBM — confirmado
  en estados reales — así que este mecanismo y la detección por titular/RFC de arriba
  DEBEN resolver a la misma categoría o el mismo traspaso se reparte de forma
  inconsistente entre dos categorías según qué texto imprimió el estado ese mes; esto
  pasó de verdad, ver regla 3 abajo). Optimax/Allianz (aportación recurrente, no
  traspaso entre cuentas propias) usa un mecanismo aparte y sigue siendo "Inversión"
  (EXPENSE) — no confundir los dos.
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
3. BBVA TDC, hueco de noviembre 2023 (**resuelto** 2026-08-12): se creyó permanentemente
   perdido — los dos archivos "Noviembre 2023"/"Diciembre 2023" en el Drive conectado
   eran el mismo PDF de diciembre (confirmado por md5) — y se cubrió con una transacción
   de ajuste sintética en `manual_data.py` (`MANUAL_TRANSACTIONS`). El usuario encontró y
   subió el estado real ("Noviembre 2023 w"); se importó, reconcilia exacto contra ambos
   lados del hueco (Saldo Inicial $18,985.52 == Saldo al Corte de octubre; Saldo al Corte
   $7,038.29 == Saldo Inicial de diciembre), y se eliminó la transacción sintética. Este
   patrón (transacción de ajuste documentada citando ambos estados reales, revertible en
   cuanto aparezca el estado real) queda como precedente para huecos futuros genuinamente
   irrecuperables — `MANUAL_TRANSACTIONS` y `_KNOWN_CHAIN_GAPS` (`importers/bbva_credit.py`)
   se dejaron vacíos pero vivos, no se borraron.
4. Traspasos a/desde GBM y Bitso (confirmado con el usuario, 2026-08-12): son traspaso
   puro, NO gasto ni ingreso — mismo tratamiento que mover dinero entre cuentas propias
   de banco. Categoría "Transferencia entre Cuentas Propias" (`app/rules.py`,
   `INVESTMENT_ACCOUNT_TRANSFER_PATTERNS`). Antes 16 de 28 traspasos GBM reales caían en
   "Inversión" (EXPENSE, sí contaba como gasto) y los otros 12 en "Transferencia" —
   inconsistente, dependía de si el estado de BBVA imprimía "GBM" o mi propio nombre ese
   mes — no una decisión, un bug. Corregido retroactivamente en la DB; el efecto medido
   fue de ~$1.2M MXN menos de "gasto" histórico y la tasa de ahorro histórica pasó de
   -14.41% a +7.57% (ver `app/savings_goal.py`). Optimax/Allianz (aportación recurrente
   al producto, no traspaso entre cuentas propias) NO cambió — sigue en "Inversión".

## Abierto / sin resolver

- **Despliegue a producción**: código y config listos (`backend/app/auth.py`,
  `backend/Dockerfile`, `fly.toml`, `frontend/functions/api/[[path]].ts`, `DEPLOY.md`) pero
  el deploy real (crear la app en Fly.io, el proyecto en Cloudflare Pages, subir
  `finanzas.db` al volumen) no se ha ejecutado — requiere las cuentas del usuario. El
  flujo de importar estados de cuenta nuevos una vez desplegado sigue siendo local
  (correr el import contra el `finanzas.db` local y volver a subirlo al volumen; no
  existe todavía un endpoint para subir el archivo crudo por HTTP contra el backend
  remoto — ver "Importar estados de cuenta nuevos" en `DEPLOY.md`).
- **Balagan, mecánica de rescate**: pregunté si "recuperar la inversión" significa
  exactamente $75,000 (valor original, Cláusulas QUINTA/SEXTA) o un monto ajustado por
  desempeño — sin confirmar todavía. No asumir ninguna de las dos en cálculos de "cuánto
  puedo recuperar".
- **Categorización automática de gasto**: implementada (`app/rules.py`, `app/classify.py`),
  validada contra datos reales de BBVA y AMEX. Cobertura parcial — sigue creciendo con
  cada corte, per el propio principio del kit ("al tercer corte casi todo se clasifica
  solo"). AMEX está excluida del matcher de traspasos por titular/RFC (ver regla dura
  arriba) porque su CSV trae el nombre del dueño de tarjeta en cada renglón.
- **Hard balance validation**: implementada donde existe un total impreso real contra
  el cual cuadrar, verificado con datos reales de cada fuente:
  - BBVA: cargos/abonos + cadena de saldos entre estados.
  - BBVA TDC: cargos/abonos contra RESUMEN de cada estado + cadena de saldos entre
    estados (ambas plantillas — ver tabla de cuentas arriba). Cadena completa, sin
    huecos (ver tabla de cuentas arriba sobre noviembre 2023).
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
categorías, y el catálogo de widgets (`docs/03-widgets.md`) como referencia general.
El dashboard (`frontend/`) terminó siendo una app real (React + Vite) contra la API en
vivo, no el patrón de archivo único del kit ni un HTML estático — decisión ya tomada,
ver la tabla de arriba.

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

Dashboard (con la API arriba corriendo en el puerto 8000):

```bash
cd frontend
npm install
npm run dev
```

## Tono

Directo y técnico, sin jerga innecesaria. Cero moralina, cero lenguaje de coach. Los
textos y el análisis financiero son descriptivos y precisos, no motivacionales.
