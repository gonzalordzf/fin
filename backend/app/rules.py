"""Auto-categorization: ordered regex rules matched against a transaction's
raw_description + description. First match wins. Anything left unmatched
stays uncategorized — /spending-by-category already reports that under
"Sin categoría", and force-assigning a literal "Otros" row would hide the
signal of what still needs a rule.

Seeded and validated against this user's real statements — not guessed from
the shared kit's generic examples. Patterns marked "not yet seen in real
data" are best-effort based on institution names and should be confirmed
once a real matching transaction shows up.

Re-validated against the full real BBVA history (18 statements, Dec 2024-
Aug 2026) plus AMEX/Balagan/Optimax/Shareworks/Bitso/AFORE: this surfaced
that every leading-anchor (`^`) and leading-\b pattern here was silently
matching zero real transactions, because the haystack is raw_description +
description and raw_description never starts with the merchant text
(BBVA's own date prefix, AMEX's dict-repr CSV row) nor has a clean word
boundary before words BBVA's PDF extraction concatenates without a space
(e.g. "0022386NOMINA", "RECIBIDOGBM"). Fixed by dropping the anchors/
boundaries that don't survive that concatenation — see inline comments on
SELF_PAYMENT_RULES, INVESTMENT_INSTITUTION_PATTERNS, INCOME_RULES, and the
Transporte/Efectivo entries in MERCHANT_RULES.
"""

from __future__ import annotations

import datetime
import re

from sqlalchemy.orm import Session

from app.models import Category, CategoryKind, SpendFrequency, Transaction

SELF_PAYMENT_RULES: list[tuple[str, str]] = [
    # Paying off your own credit card moves money between your own
    # accounts, it isn't spending — validated on both sides of the same
    # real event: BBVA's "AMERICAN EXPRESS 01429 -309,542.23" line and
    # AMEX's own "GRACIAS POR SU PAGO CON CARGO A BBVA" line.
    #
    # No leading ^ anchor: haystack is raw_description + description, and
    # raw_description always starts with something else first (BBVA's own
    # date prefix, AMEX's dict-repr CSV row), so an anchored pattern here
    # never matches anything — confirmed against the full 18-month BBVA
    # history, where these two patterns matched zero of 41 real occurrences
    # until the anchor was dropped.
    (r"PAGO TARJETA DE CREDITO", "Pago de Tarjeta de Crédito"),
    (r"AMERICAN EXPRESS \d+", "Pago de Tarjeta de Crédito"),
    (r"GRACIAS POR SU PAGO", "Pago de Tarjeta de Crédito"),
    # BBVA TDC's own abono line for a payment made via the bbva móvil app —
    # confirmed on real statements, e.g. "BMOVIL.PAGO TDC -$8,050.50"
    # (abono, our sign flips it positive). This account's own printed
    # balance-chain validation (see parsers/bbva_credit.py) already
    # confirms these always pair off against an equal previous_balance —
    # it's paying down the card, not income.
    (r"BMOVIL\.?\s*PAGO\s*TDC", "Pago de Tarjeta de Crédito"),
    # A credit for a disputed/unrecognized charge is a refund, not a
    # transfer — validated: real AMEX line "CREDITO POR CARGO NO
    # RECONOCIDO".
    (r"CREDITO POR CARGO NO RECONOCIDO", "Reembolsos"),
    # Order matters and is load-bearing: "COMISION POR PAGO DEVUELTO"
    # contains "PAGO DEVUELTO", and SELF_PAYMENT_RULES is evaluated before
    # MERCHANT_RULES in full, so the commission has to be caught here — not
    # in MERCHANT_RULES — or the broader rule below would swallow it.
    (r"COMISION POR PAGO DEVUELTO", "Impuestos y Comisiones Bancarias"),
    # A bounced card payment reversing back onto the balance: not spending,
    # it's the undo of a card payment, so it belongs with them and nets out.
    (r"PAGO DEVUELTO", "Pago de Tarjeta de Crédito"),
]

INVESTMENT_INSTITUTION_PATTERNS: list[str] = [
    # No leading \b: BBVA's own PDF text extraction concatenates "SPEI
    # RECIBIDOGBM" as one word with no space before the institution name
    # (confirmed on 4 real GBM withdrawal transactions — \bGBM\b matched 0
    # of them since there's no word boundary between "O" and "G").
    r"GBM\b",
    r"BITSO\b",  # not yet seen in a real contribution transaction — best effort
    # Confirmed on real BBVA TDC charges ("ALLIANZ MEXICO CR"): the fixed
    # monthly Optimax contribution, paid by credit card — not car
    # insurance (Qualitas/ANA, which stays in Transporte, are unrelated).
    r"ALLIANZ\b",
    r"OPTIMAX\b",  # not yet seen in a real contribution transaction — best effort
]
_INVESTMENT_CATEGORY = "Inversión"

INCOME_RULES: list[tuple[str, str]] = [
    # No leading \b, same concatenation issue as GBM above: BBVA renders
    # payroll SPEI as e.g. "0022386NOMINA" with no space — confirmed on 47
    # of 47 real payroll deposits, all invisible to \bNOMINA\b.
    (r"NOMINA\b", "Nómina"),
    # NOT the AFORE SURA retirement account (that's a separate account,
    # imported on its own from AFORE's own PDF, never touches BBVA) — the
    # user confirmed these "SURA INVESTMENT MANAGEMENT MEXICO" SPEI
    # deposits into BBVA (recurring ~twice a year) are payouts from an
    # employer caja de ahorro/savings fund, not an investment contribution.
    # Previously miscategorized as "Inversión" before this was confirmed.
    (r"\bSURA\b", "Fondo de Ahorro"),
]

# Recurring transfers to named individuals — not merchants, but confirmed
# by the user as fixed, recognizable real expenses that BBVA's generic
# "PAGO CUENTA DE TERCERO"/"SPEI ENVIADO" descriptions don't otherwise
# distinguish from one-off personal transfers.
KNOWN_PERSON_RULES: list[tuple[str, str]] = [
    (r"REMIS", "Salud"),  # Begoña Remis — psicóloga, recurring
    # Renta del depto actual. El patrón es el apellido, no el nombre
    # completo, a propósito: hasta ~2025 el depósito iba al papá de Ceci
    # (fallecido desde entonces) y después a ella — dos personas distintas,
    # el mismo hecho económico, ambas cubiertas por el apellido.
    (r"BATIZ", "Vivienda"),
    # Arreola tiene vigencia (ver DATED_PERSON_RULES): la relación de
    # roomies terminó en ago-2023 y siguen siendo amigos.
    # Arrendadora de ene-oct 2024, $28,000-29,000/mes (en octubre partido
    # en dos SPEI de $14,500). El traspaso a los Batiz es exacto: octubre
    # es el último mes con ella, noviembre el primero con Luis Eduardo
    # Batiz Campbell por el mismo monto — sin traslape ni hueco.
    (r"RAMONELL", "Vivienda"),
    # Los roomies de 2023-2024 (Eugenia González, José Manuel Marentes) NO
    # van aquí — su relación tiene fecha de corte, ver DATED_PERSON_RULES.
    (r"CASTILLO MEADE", "Regalos"),  # Maria Luis Castillo Meade — regalo de boda, one-off
    # Full name, not just "RAMOS": a "Daniel Ramos" appears unrelated in
    # the World Cup ticket reimbursement thread — matching on the surname
    # alone would collide with that real person.
    (r"CLARA RAMOS", "Vivienda"),  # servicio de limpieza doméstica, recurring
]

# Renta: cubre los dos lados del mismo hecho económico y por eso va a
# "Vivienda" sin importar el signo — la renta que Gonzalo paga (salida) y
# lo que los roomies le depositan por su parte (entrada, memos tipo "RENTA
# DANIEL MARZO 2026", "renta roy", "RENTA JULIO"). Netear ambos lados es
# el único costo de vivienda que significa algo: el bruto sobreestima
# ~$17,400/mes desde que hay roomies. Requiere que spending-by-category
# sume el neto de la categoría y no solo los cargos — ver main.py.
# Sin \b inicial, por la misma concatenación de BBVA que ya obligó a
# quitarlo en NOMINA y GBM: los memos de salida vienen pegados a la fecha
# ("0801240Renta FMDO 108 603", "0603240Renta Marzo"), así que no hay
# frontera de palabra antes de "Renta". Los depósitos ENTRANTES de roomies
# sí traen espacio ("Enero 2024 Renta"), de modo que \bRENTA\b capturaba
# solo el lado que entra y ninguno de los que sale — exactamente la
# asimetría que hacía que 2024 reportara $1,718/mes de vivienda neta.
# El \b final se conserva: evita machear "rentabilidad" y similares. La
# ausencia de \b inicial sí necesita guardia: sin ella, "RENTA" matchea
# dentro de "CUARENTA" (encontrado en un cargo real de BBVA TDC, "REST
# CUARENTA" — un restaurante, no renta) porque \b no exige que el carácter
# previo sea un no-letra, solo que haya una transición letra/no-letra en
# algún punto. El lookbehind negativo exige eso explícitamente: rechaza
# cuando el carácter anterior es una letra (CUARENTA) pero sigue aceptando
# cuando es un dígito (concatenación de BBVA, "0801240Renta") o un espacio
# ("Enero 2024 Renta").
RENT_PATTERNS: list[str] = [r"(?<![A-Za-z])RENTA\b"]
_RENT_CATEGORY = "Vivienda"

# Fijo vs variable is a per-transaction dimension, not a category-level one
# (see SpendFrequency's docstring) — "Vivienda" holds both la renta (fijo)
# and a one-off home-repair purchase (variable); "Salud" holds both la
# psicóloga (fijo, mensual) and a random doctor visit (variable). Patterns
# below are the ones the user named directly (seguros, gastos de hogar
# recurrentes tipo la luz, la renta, la psicóloga) plus the ones already
# confirmed recurring by their own rule comments elsewhere in this file —
# not a guess at every possibly-recurring merchant, only the ones with an
# actual basis. Matched the same way as every other rule here: against
# raw_description + description, case-insensitive.
FIXED_EXPENSE_PATTERNS: list[str] = [
    *RENT_PATTERNS,  # la renta (BATIZ/RAMONELL/CLARA RAMOS below cover named-payee renta too)
    r"BATIZ",
    r"RAMONELL",
    r"CLARA RAMOS",  # servicio de limpieza doméstica — recurring per its own rule comment
    r"REMIS",  # Begoña Remis — psicóloga, recurring
    r"\bCFE\b",  # luz — CFE SUM SERV BAS (MU/CR MU), domiciliado, confirmado en BBVA TDC
    r"CONDOMIDRACO",  # cuota de mantenimiento del condominio, mensual
    r"QUALITAS|ANA COMPA",  # seguro de auto — pólizas, no compras puntuales
]

# Reglas con vigencia: (patrón, categoría, desde, hasta) — ambas fechas
# inclusivas, None = sin límite por ese lado.
#
# Existen porque una misma persona cambia de rol con el tiempo y el nombre
# solo no basta para decidir la categoría. Eugenia González y José Manuel
# Marentes fueron roomies hasta noviembre de 2024 y siguen siendo amigos:
# un depósito suyo en 2024 es su parte de la renta, pero una transferencia
# en 2025 es cualquier otra cosa (una cena, un préstamo, un regalo).
# Clasificar por nombre sin fecha metía gasto de vivienda que nunca
# existió — se detectó por una transferencia a Eugenia en nov-2025, ya
# fuera de la relación de roomies.
#
# Fuera de su ventana estos movimientos quedan SIN clasificar a propósito:
# el dato no dice de qué fueron, y adivinar es justo lo que este proyecto
# no hace. Aparecen en "Sin categoría" para revisión manual.
DATED_PERSON_RULES: list[tuple[str, str, datetime.date | None, datetime.date | None]] = [
    # Eugenia González y José Manuel Marentes NO se identifican por nombre:
    # sus depósitos de renta entran como traspasos internos de BBVA, donde
    # el estado solo imprime "BNET <id>" y nunca el nombre. En todo el
    # historial hay apenas 5 movimientos con sus nombres, y ninguno es
    # renta — el único que macheaba era una quiniela de $750 que esta misma
    # regla metía por error a Vivienda. Se identifican por su cuenta BNET,
    # ver ROOMMATE_ACCOUNT_RULES.
    # Roomie de ene-ago 2023, NO el arrendador: él le pagaba al dueño y
    # Gonzalo le transfería su parte (8 pagos, $15,450 bajando a $12,000,
    # memos "Renta"/"La fija"). Sin esta regla 2023 reportaba $2,237/mes
    # de vivienda, cifra imposible que fue la que delató todo el hueco.
    #
    # La vigencia cierra en ago-2023 porque los pagos de renta se detienen
    # ahí y en ene-2024 empieza Ramonell.
    (r"ARREOLA", "Vivienda", None, datetime.date(2023, 12, 31)),
    # Los 6 SPEI a albo de sep-2025 ($166,000 total, memo solo "gonzalo")
    # son la aportación al Grupo Arreola Herrera Fund I — confirmado por el
    # usuario y respaldado por el contrato de préstamo convertible (ver
    # manual_data.py).
    (r"ARREOLA", "Inversión", datetime.date(2025, 9, 1), datetime.date(2025, 9, 30)),
    # $23,250 de ene-2026, memo "colchones tambires refrigerador": muebles
    # que Gonzalo le compró a Chema para el depto — confirmado por el
    # usuario. Nótese que NO va a Vivienda: esa categoría mide renta
    # recurrente, y meter una compra única de muebles ahí infla el costo
    # de vivienda de ese mes sin ser parte del patrón mensual.
    (r"ARREOLA", "Compras", datetime.date(2026, 1, 1), datetime.date(2026, 1, 31)),
    # Pago parcial de los intereses del préstamo mercantil de Cañadas de
    # Malta (12%/año sobre $25,000, ver manual_data.py) — memo "Pago Deuda
    # 2", 26-jul-2025, confirmado por el usuario: es solo una parte de lo
    # que le deben, el resto sigue en disputa. Va a "Ingreso por Inversión"
    # y NO se suma al balance de la cuenta (que se queda al valor nominal
    # del contrato, no se sabe si el resto se cobra).
    (r"0117384335", "Ingreso por Inversión", datetime.date(2025, 7, 20), datetime.date(2025, 7, 31)),
    # Aportación de capital a Balagan (ver app/parsers/balagan.py,
    # INITIAL_INVESTMENT_MXN): SPEI ENVIADO BANORTE, 02-dic-2024, $75,000,
    # memo "inversion Gonzalo", beneficiario "RIVER SA DE CV" — confirmado
    # por el usuario como la razón social de Balagan. Va a "Inversión" igual
    # que las aportaciones a GBM/Bitso, matcheado por referencia SPEI para
    # no depender de la palabra "RIVER" (demasiado genérica para usarse
    # sola).
    (r"0073972356", "Inversión", datetime.date(2024, 12, 1), datetime.date(2024, 12, 31)),
]

# Viaje a Colombia, dic-2023: Gonzalo pagó $95,000 (+ $344.43 comisión +
# $55.11 IVA de la comisión, un solo "ORDEN DE PAGO EXTRANJERO", ref.
# 8217308.1002.01) y el grupo le fue regresando su parte — confirmado por
# el usuario como pass-through, igual que el Mundial. A diferencia del
# Mundial no hay un monto limpio por persona que identifique los
# reembolsos sin nombre, así que solo se cuentan los que el propio memo
# marca como del viaje ("Colombia", "Cartagena") — ver CLAUDE.md: el resto
# de los reembolsos, si los hay, quedan pendientes de identificar, no se
# adivinan.
COLOMBIA_TRIP_REF_RE = re.compile(r"8217308\.1002\.01")
COLOMBIA_TRIP_MEMO_RE = re.compile(r"COLOMBIA|CARTAGENA", re.IGNORECASE)
_COLOMBIA_TRIP_CATEGORY = "Viajes"


def _colombia_trip_match(text: str, amount: float) -> str | None:
    if COLOMBIA_TRIP_REF_RE.search(text):
        return _COLOMBIA_TRIP_CATEGORY
    # Solo entradas: confirmado con datos reales que un match sin signo
    # atrapaba consumo genuino de un viaje aparte a Cartagena en feb-2024
    # (AMEX incluye la ciudad del comercio en la descripción — "ALQUIMICO
    # CARTAGENA", "JUAN VALDEZ ... BOGOTA" — nada que ver con el reembolso
    # de dic-2023). Los reembolsos reales del grupo son entradas ("BNET ...
    # colombia", "SPEI RECIBIDO... CARTAGENA LTV"); un cargo de AMEX en
    # Cartagena siempre es una salida, así que el signo basta para separar
    # ambas cosas sin acotar por fecha.
    if amount > 0 and COLOMBIA_TRIP_MEMO_RE.search(text):
        return _COLOMBIA_TRIP_CATEGORY
    return None


# Roomies identificados por su cuenta interna de BBVA (BNET), no por
# nombre: los traspasos entre cuentas BBVA no imprimen al remitente, así
# que el id es la única señal estable de quién es.
#
# El id solo no basta —un roomie también manda dinero por otras razones
# ("Tahoe", "Splitwise", "Padel")— así que además se exige que el memo
# parezca renta: la palabra renta, un nombre de mes, o una fracción de
# mes. Los memos reales son irregulares y por eso `RENTA\b` sola fallaba:
# "1ra Mayo", "Mayo 2", "Julio", "Mitad Agosto y wifi", "Sept 1ra parte",
# "2Q Sept y servicios", "Renta1" (donde el dígito pegado rompe \b).
#
# Ventanas confirmadas contra los movimientos reales:
#   1537142938  Roy, roomie continuo desde ago-2023
#   1539193445  roomie abr-jul 2024, $6,250 quincenales
#   2631246879  Eugenia González (confirmado por "Transf a EUGENIA A")
ROOMMATE_ACCOUNT_RULES: list[tuple[str, datetime.date | None, datetime.date | None]] = [
    ("1537142938", datetime.date(2023, 8, 1), None),
    ("1539193445", datetime.date(2024, 4, 1), datetime.date(2024, 7, 31)),
    ("2631246879", datetime.date(2024, 7, 1), datetime.date(2024, 11, 30)),
]

_RENT_MEMO_RE = re.compile(
    r"RENTA|MITAD|MEDIA|\b1RA\b|\b2Q\b|PARTE|"
    r"ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SEPT|OCTUBRE|NOVIEMBRE|DICIEMBRE",
    re.IGNORECASE,
)


# Memos del Mundial que no dicen "mundial". El usuario confirmó uno por
# uno estos movimientos como boletos comprados para el grupo y sus
# reembolsos, pero los memos son coloquiales ("Y SI Si", "vamos mexico
# caraj", "ysisiPibana") o genéricos ("tickets", "boletos", "entradas
# semi"), así que ningún patrón de WORLD_CUP_PATTERNS los alcanzaba.
#
# Acotado a la ventana del torneo a propósito: "boleto"/"ticket" fuera de
# ella es cualquier otra cosa (hay un "boleto Malu" en ene-2025 que no
# tiene relación). El límite inferior es abr-2026, cuando arranca la
# compra a la Federación.
_WORLD_CUP_WINDOW = (datetime.date(2026, 4, 1), datetime.date(2026, 8, 31))
_WORLD_CUP_MEMO_RE = re.compile(
    r"BOLETO|TICKET|\bTKT\b|ENTRADAS?\s+SEMI|Y\s*SI\s*SI|YSISI|VAMOS\s+MEXICO",
    re.IGNORECASE,
)

# Referencia propia de la Federación en los SPEI ("Cubx6020823502114").
# No lleva ventana de fechas porque el identificador es inequívoco por sí
# solo, y es la única forma de capturar el SPEI DEVUELTO de $403,900: ese
# cargo se duplicó y se revirtió el mismo día con la misma referencia, así
# que sin la devolución la categoría queda inflada por un pago que nunca
# ocurrió.
_WORLD_CUP_REF_RE = re.compile(r"CUBX\d+", re.IGNORECASE)

# Boletos de la ceremonia inaugural, $243,900 (1-may-2026), confirmado por
# el usuario — reembolsado por el grupo desde finales de abril. El memo
# ("Transf a Servicios") no dice nada del Mundial y es genérico, así que
# se matchea por el número de referencia SPEI, la única forma de
# identificarlo sin arriesgar falsos positivos con otro "Transf a
# Servicios" que no tenga relación en el futuro.
_WORLD_CUP_OPENING_REF_RE = re.compile(r"0040561816")

# Precio unitario del boleto: $403,900 / 20 = $20,195 exactos. Los 16
# reembolsos del grupo llegan justo por ese monto y muchos traen memos que
# no dicen nada del Mundial ("Daniel Ramos", "Monica C", "Transferencia",
# "QUIERE VOLAR QUIERE VOLAR"). El usuario los confirmó uno por uno; el
# monto exacto dentro de la ventana es la señal que los identifica sin
# tener que listar nombres.
_WORLD_CUP_TICKET_PRICE = 20195.00

# Tres SPEI RECIBIDOSTP grandes de 2026 (may/jul) sin nombre de beneficiario
# capturado por el PDF de BBVA — el usuario los confirmó como parte del
# reembolso/pago del Mundial, sin poder precisar de cuál boleto o de quién
# exactamente. Igual que la ceremonia inaugural, se matchean por referencia
# SPEI (la única señal disponible) en vez de por texto, que aquí ni existe.
_WORLD_CUP_STP_REFS_RE = re.compile(r"0100594788|0127990873|0153962721")


def _world_cup_memo_match(text: str, when: datetime.date, amount: float) -> str | None:
    if (
        _WORLD_CUP_REF_RE.search(text)
        or _WORLD_CUP_OPENING_REF_RE.search(text)
        or _WORLD_CUP_STP_REFS_RE.search(text)
    ):
        return _WORLD_CUP_CATEGORY
    lo, hi = _WORLD_CUP_WINDOW
    if not (lo <= when <= hi):
        return None
    if _WORLD_CUP_MEMO_RE.search(text):
        return _WORLD_CUP_CATEGORY
    if abs(abs(amount) - _WORLD_CUP_TICKET_PRICE) < 0.01:
        return _WORLD_CUP_CATEGORY
    return None


def _roommate_rent_match(text: str, when: datetime.date) -> str | None:
    for bnet_id, valid_from, valid_until in ROOMMATE_ACCOUNT_RULES:
        if valid_from is not None and when < valid_from:
            continue
        if valid_until is not None and when > valid_until:
            continue
        if f"BNET {bnet_id}" in text and _RENT_MEMO_RE.search(text):
            return _RENT_CATEGORY
    return None


def _first_dated_match(text: str, when: datetime.date) -> str | None:
    for pattern, category_name, valid_from, valid_until in DATED_PERSON_RULES:
        if valid_from is not None and when < valid_from:
            continue
        if valid_until is not None and when > valid_until:
            continue
        if re.search(pattern, text, re.IGNORECASE):
            return category_name
    return None

# World Cup 2026: ticket purchases (mostly from Federación Mexicana de
# Fútbol) and reimbursements from the friend group that paid Gonzalo back.
# One category, kind=EXPENSE, covers both directions on purpose — the
# outflow (buying tickets) and the inflow (getting reimbursed) are tagged
# the same, so /spending-by-category's amount<0 filter naturally nets out
# to only the real personal cost (confirmed against real data: of
# $698,370 paid to the Federación across 3 statements, $672,386 came back
# from named reimbursers, leaving ~$25,984 that was genuinely Gonzalo's
# own tickets — that residual is what should show as "gasto", not the
# gross $698,370). Matched only on unambiguous markers confirmed against
# real memos; vaguer ones ("gonzalo", "Y SI Si") are left uncategorized
# rather than guessed.
WORLD_CUP_PATTERNS: list[str] = [
    r"MUNDIAL", r"\bFIFA\b", r"\bFWC\b", r"FEDERACION MEXICANA DE FUTBOL",
    # Ticket broker (Calabasas, CA) the user confirmed as the semifinal
    # tickets bought on AMEX and reimbursed by SPEI — the two real charges
    # ($209,207 / $57,150) are World Cup, not generic entertainment.
    r"SPOTLIGHT TICKET",
]
_WORLD_CUP_CATEGORY = "Mundial"

MERCHANT_RULES: list[tuple[str, str]] = [
    # Transporte
    (r"\bUBER TRIP\b", "Transporte"),
    # No leading ^: same anchor-vs-haystack-prefix issue as SELF_PAYMENT_RULES
    # above — confirmed 0/24 real AMEX toll charges matched with the anchor.
    (r"PASE ", "Transporte"),  # toll-road tag charges
    (r"MUEVE CIUDAD", "Transporte"),
    (r"RETIRO SIN TARJETA", "Efectivo (ATM)"),
    # Delivery — kept separate from restaurants, per docs/02-categorias.md:
    # it's cut back differently in a real budget squeeze.
    (r"UBER EATS", "Delivery"),
    (r"RAPPI\*RAPPI", "Delivery"),
    (r"RAPPI\*PRIME", "Delivery"),
    # Alimentos y Supermercado
    (r"SUPERAMA", "Alimentos y Supermercado"),
    (r"\bOXXO\b", "Alimentos y Supermercado"),
    # Restaurantes y Café — generic markers plus specific merchants seen in
    # this user's real statements
    (r"\bREST(AURANTE)?\b", "Restaurantes y Café"),
    (r"\bCAFE\b", "Restaurantes y Café"),
    (r"\bBAR\s", "Restaurantes y Café"),
    (r"MERCADOPAGO\*(PIOLA|MORAMORA)\b", "Restaurantes y Café"),
    (r"BZPAY\*RESTAURANTE", "Restaurantes y Café"),
    (r"NETPAY\*ROKAI", "Restaurantes y Café"),
    (r"SR PAGO\*REST", "Restaurantes y Café"),
    (r"TACOS ATARANTADOS", "Restaurantes y Café"),
    (r"^TORINO\b", "Restaurantes y Café"),
    (r"CALIFA (MAZARYK|PALMAS)", "Restaurantes y Café"),
    (r"MI COMPA CHAVA", "Restaurantes y Café"),
    (r"CENTRO LIBANES", "Restaurantes y Café"),
    (r"COM RAP MAIZAJO", "Restaurantes y Café"),
    (r"CHIQUITO CAFE", "Restaurantes y Café"),
    (r"EL MANDARINO", "Restaurantes y Café"),
    (r"LA PESCADERIA", "Restaurantes y Café"),
    # Servicios y Suscripciones
    (r"NETFLIX", "Servicios y Suscripciones"),
    (r"APPLE\.COM", "Servicios y Suscripciones"),
    (r"AMAZON PRIME", "Servicios y Suscripciones"),
    (r"OURARING", "Servicios y Suscripciones"),
    (r"TELEFONOS DE MEXICO", "Servicios y Suscripciones"),
    (r"CONEKTA\*TOTALPASS", "Cuidado Personal"),
    (r"GYMPASS", "Cuidado Personal"),
    # Same vendor, three different descriptors across the years (CONEKTA
    # gateway, the tilde variant, and their own SAPI entity) — all one gym
    # membership, confirmed by the matching amounts and cadence.
    (r"TOTAL\s*[~*]?\s*PASS", "Cuidado Personal"),
    # Entretenimiento
    (r"PLAYSTATION NETWORK", "Entretenimiento"),
    (r"TICKETMASTER", "Entretenimiento"),
    (r"CINEPOLIS|CINEMEX", "Entretenimiento"),
    # Matched on the ASCII-safe fragment: AMEX's own export mojibakes this
    # merchant ("CONECTAPP*PÃ DELRANGERS"), so the accented name can't be
    # relied on. Padel court bookings.
    (r"DELRANGERS", "Entretenimiento"),
    # Compras
    (r"AMAZON MX", "Compras"),
    (r"LIVERPOOL", "Compras"),
    (r"FANTASIAS", "Compras"),
    (r"APPLE (STORE|MEXICO)", "Compras"),
    (r"UNIQLO", "Compras"),
    (r"ADIDAS", "Compras"),
    (r"SWATCH", "Compras"),
    (r"HELLY HANSEN", "Compras"),
    (r"\bREI #", "Compras"),
    (r"MOBLUM", "Compras"),  # muebles
    (r"EMMASLEEP", "Compras"),  # colchón
    # Viajes
    (r"AEROMEXICO", "Viajes"),
    (r"AIR FRANCE", "Viajes"),
    (r"HTL\*", "Viajes"),
    (r"AIRBNB", "Viajes"),
    (r"EXPEDIA", "Viajes"),
    (r"CELEBRITY CRUISES", "Viajes"),
    (r"COPA AIRLINES", "Viajes"),
    (r"VIVAAEROBUS", "Viajes"),
    (r"Concesionaria Vuela", "Viajes"),  # Volaris
    (r"PALACE RESORTS", "Viajes"),
    (r"\bHERTZ\b", "Viajes"),
    (r"VAIL SKI PASS|VAL THORENS", "Viajes"),
    # Aerolíneas y hoteles (bloque agregado al ampliar el historial de AMEX
    # a 2023-2026) — marcas globales, sin ambigüedad razonable.
    (r"VUELING|ITA AIRWAYS|EASYJET|IBERIA\.COM|LATAM AIRLINES", "Viajes"),
    (r"EUROSTARS|LIVE AQUA|HOTEL HAMPTON|HOTEL MIRAMAR", "Viajes"),
    (r"AIRALO", "Viajes"),  # eSIM de datos para viajar
    (r"WORLD DUTY FREE|DUTY FREE WALKTHROUGH", "Viajes"),
    # Ropa y calzado — marcas globales
    (r"MASSIMO DUTTI|GUTTERIDGE|\bZARA\b|LULULEMON|AMERICAN EAGLE", "Compras"),
    (r"\bNIKE\b|PAYPAL \*PUMAMEXICOS|\bON INC\b", "Compras"),
    (r"AMZN MKTP|PALACIODEHIERRO|MERCADO LIBRE|TEMU\.COM", "Compras"),
    # Salud — hospitales, laboratorios y consultorios reales
    (r"HOSPITAL ANGELES|LAB MEDICO DEL CHOPO|MED CENTRO DE FISIOTERA", "Salud"),
    # Sin el prefijo "D": AMEX trunca a veces a "R GABRIEL ARRIOLA L" en el
    # mismo doctor — confirmado por nombre y monto en el mismo rango.
    (r"GABRIEL ARRIOLA", "Salud"),
    # Gasolineras
    (r"GASOLINERA|PITS GAS EST", "Transporte"),
    # Restaurantes (bloque tardío: comercios confirmados al ampliar el
    # historial de AMEX a 2023-2026)
    (r"AMIGAS CONDESA", "Restaurantes y Café"),
    (r"GOCCIA|RISTORANTE CUCINA TORCI", "Restaurantes y Café"),  # Florencia
    (r"BLACK'?S PUB|HY'?S STEAKHOUSE|BRIAR ROSE CHOPHOUSE", "Restaurantes y Café"),
    (r"MAREA FISHERS|LA NAVAL|CANTINA DEL BOSQUE", "Restaurantes y Café"),
    (r"NETPAY\*TAQUERIA ORINOCO|NETPAY\*BARRA GRANA", "Restaurantes y Café"),
    (r"VIVA\*CERVEJARIA|VIVA\*BOSCO", "Restaurantes y Café"),  # Lisboa/Porto
    # Entretenimiento — boletos y vida nocturna
    (r"VIAGOGO|ATG TICKETS", "Entretenimiento"),
    (r"SPACE CLUB|\bBRESH\b|TEATRO .*ENTERTAINMEN", "Entretenimiento"),
    # El restaurante en el que el usuario es inversionista — consumo propio
    # en el local, no tiene relación con la aportación de capital (esa vive
    # en AlternativeInvestmentEntry, no en Transaction, así que no colisiona).
    (r"BALAGAN", "Restaurantes y Café"),
    (r"PAVIRO", "Restaurantes y Café"),
    # Impuestos y Comisiones Bancarias — cuotas y cargos de la propia AMEX
    (r"CUOTA ANUAL", "Impuestos y Comisiones Bancarias"),
    (r"IVA APLICABLE", "Impuestos y Comisiones Bancarias"),
    (r"AJUSTE DE DEBITO", "Impuestos y Comisiones Bancarias"),
    (r"\*AMEX INTERNET", "Impuestos y Comisiones Bancarias"),
    (r"RET CAJ OTRO BCO|RETIRO CAJERO AUTOMATICO", "Efectivo (ATM)"),
    # Transporte
    (r"\bDIDI\b", "Transporte"),
    # Aseguradoras de auto (Qualitas, ANA) — Transporte, no Vivienda: son
    # pólizas vehiculares, confirmado por el nombre de la aseguradora.
    (r"QUALITAS|ANA COMPA", "Transporte"),
    # Vivienda
    (r"ROTOPLAS", "Vivienda"),
    # Administración del condominio: memos "Pago FMDO 603", "Recibo
    # 578245". FMDO 603 es el mismo identificador de departamento que
    # aparece en el memo de renta de Ramonell ("Renta FMDO 108 603"), así
    # que es la cuota de mantenimiento del depto, no un proveedor suelto.
    (r"CONDOMIDRACO", "Vivienda"),
    # CFE (Comisión Federal de Electricidad) — recibo de luz, confirmado en
    # cargos reales de BBVA TDC ("CFE SUM SERV BAS MU", "CFE SUM SERV BAS
    # CR MU"), ambos con el mismo número de tarjeta digital en el memo
    # (domiciliado), un mes tras otro.
    (r"\bCFE\b", "Vivienda"),
    # Telcel — telefonía, confirmado en cargos reales de BBVA TDC
    # ("TELCEL MEXICO CR"), recurrente mes a mes con montos similares.
    (r"\bTELCEL\b", "Servicios y Suscripciones"),
    # Devolución del SAT ("HACIENDA TE DEVUELVE", Tesorería de la
    # Federación). Es dinero que regresa, no ingreso nuevo.
    (r"HACIENDA TE DEVUELVE", "Reembolsos"),
    # Pagos de una póliza de seguro (confirmado por el usuario). Entran
    # como Reembolsos y no como ingreso: el análisis de ingreso suma solo
    # Nómina y Fondo de Ahorro, así que esto no infla el ingreso, y como
    # Reembolsos es categoría INCOME tampoco entra al gasto.
    (r"\bMETLIFE\b", "Reembolsos"),
    # Transferencias familiares (confirmado por el usuario: no son ingreso
    # real). OJO — Daniel Rodríguez Fierro NO es el titular: docs/04-gotchas
    # advierte justamente que hay personas de apellido Fierro que no son
    # Gonzalo, así que esto nunca debe tratarse como traspaso propio.
    (r"DANIEL RODRIGUEZ FIERRO", "Reembolsos"),
    # Segundo lote (offset 60-140 en volumen de AMEX sin clasificar):
    # restaurantes/bares identificables por nombre propio, no genéricos.
    (r"\bMAIZAJO\b", "Restaurantes y Café"),  # taquería CDMX, ya cubierta con prefijo "COM RAP" — esta es la variante sin prefijo
    (r"ROOFCHAPULTEPEC", "Restaurantes y Café"),  # rooftop bar, Chapultepec
    (r"PRIME MASARYK", "Restaurantes y Café"),  # steakhouse, Masaryk CDMX
    (r"GIAMPIETRO PIZZERIA", "Restaurantes y Café"),  # Breckenridge, viaje de esquí
    (r"TST\* THE PROSPECTOR", "Restaurantes y Café"),  # Breckenridge, viaje de esquí
    (r"LA FOLIE DOUCE", "Restaurantes y Café"),  # Val Thorens, viaje de esquí
    (r"THE HOFF FUENCARRAL", "Restaurantes y Café"),  # bar de cerveza artesanal, Madrid
    (r"RAW BAJA", "Restaurantes y Café"),  # Cabo San Lucas
    (r"BARRA 33\b", "Restaurantes y Café"),
    # Entretenimiento — torneos de tenis (US Open, BNP Paribas/Indian
    # Wells) y mini-golf, todos nombres propios sin ambigüedad razonable.
    (r"ARAMARK US OPEN", "Entretenimiento"),
    (r"INDIAN WELLS TENNIS GAR", "Entretenimiento"),
    (r"LEGENDS@BNP PARIBAS", "Entretenimiento"),
    (r"TST\* PUTTSHACK", "Entretenimiento"),
    # Cuidado Personal
    (r"SALON GARDENIA", "Cuidado Personal"),
    (r"DAAVA - TRAINING LAB", "Cuidado Personal"),
    # Viajes — hospedaje, tren y equipo de esquí ligados a viajes ya vistos
    # en otros comercios de la misma fecha/ciudad (Tahoe, Breckenridge).
    (r"NTV TERMINI", "Viajes"),  # tren Italo, estación Termini, Roma
    (r"TAHOEPOWDERHOUSE", "Viajes"),  # renta de equipo de esquí, South Lake Tahoe
    (r"\bSKI SHOP\b", "Viajes"),  # el propio texto del comercio lo dice, sin adivinar la marca
    (r"HACIENDA VISTA HERMOSA", "Viajes"),  # hotel/hacienda, Morelos
    # Compras
    (r"SCALPER STUDIO", "Compras"),  # marca mexicana de ropa
    (r"HEAVENLY PATAGONIA", "Compras"),  # tienda Patagonia en resort de esquí Heavenly
    # Alimentos y Supermercado
    (r"\bLA COMER\b", "Alimentos y Supermercado"),
    (r"TIENDA INGLESA", "Alimentos y Supermercado"),  # supermercado uruguayo
    # Tercer lote (offset 70-140): más restaurantes por nombre propio,
    # varios en las mismas ciudades de viaje ya vistas (Breckenridge/South
    # Lake Tahoe = viaje de esquí, San Miguel de Allende, Cartagena).
    (r"TST\* JACKS WIFE FREDA", "Restaurantes y Café"),  # Nueva York
    (r"TEN MILE STATION", "Restaurantes y Café"),  # lodge de esquí, Breckenridge
    (r"TST\* LARTUSI", "Restaurantes y Café"),  # Nueva York
    (r"CANTINHO PORTO", "Restaurantes y Café"),
    (r"ROJOS TAVERN", "Restaurantes y Café"),  # South Lake Tahoe, viaje de esquí
    (r"BARCELONA RINO", "Restaurantes y Café"),  # Denver
    (r"BALTHAZAR RESTAURANT", "Restaurantes y Café"),  # Nueva York
    (r"LGS PRIME STEAKHOUSE", "Restaurantes y Café"),  # La Quinta
    (r"LA TERRAZA CHA CHA CHA", "Restaurantes y Café"),
    (r"ALQUIMICO CARTAGENA", "Restaurantes y Café"),  # cargo real, no el reembolso del viaje de dic-2023 (ver _colombia_trip_match)
    (r"EL TIZONCITO", "Restaurantes y Café"),  # taquería, Cholula
    (r"\bLA VINERIA\b", "Restaurantes y Café"),
    (r"LA UNICA SMA", "Restaurantes y Café"),  # San Miguel de Allende
    (r"\bKAZUYA\b", "Restaurantes y Café"),  # Huixquilucan
    (r"ZTL\*ODETTE CUISINE", "Restaurantes y Café"),
    (r"\bTONCHIN\b", "Restaurantes y Café"),  # ramen CDMX, cubre variantes BILLPOCKET y con sucursal
    (r"BILLPOCKET\*FIERA ROOFTO", "Restaurantes y Café"),
    (r"CANTON MEXICALI", "Restaurantes y Café"),  # restaurante cantonés
    (r"SALSEIROS DO MAR", "Restaurantes y Café"),  # A Coruña
    (r"\bTRASTEVERE\b", "Restaurantes y Café"),  # restaurante italiano, Mexico
    (r"AROMI SAPORI", "Restaurantes y Café"),
    (r"NETPAY\*NEVERIA ROXY", "Restaurantes y Café"),  # nevería
    (r"MERCADOPAGO\*INSIDECAFE", "Restaurantes y Café"),  # San Miguel de Allende
    # Entretenimiento
    (r"\bOCESA\b", "Entretenimiento"),  # ticketera de eventos, México
    (r"LYRIC THEATRE", "Entretenimiento"),  # Nueva York
    (r"PLAYTOMIC\.IO", "Entretenimiento"),  # reservas de pádel
    # Salud
    (r"LAB MED POLANCO", "Salud"),
    (r"ORTOPEDISTAS ABC MEXICO", "Salud"),
    (r"DUANE READE", "Salud"),  # cadena de farmacias, Nueva York
    # Impuestos y Comisiones Bancarias
    (r"CARGO POR PAGO TARD", "Impuestos y Comisiones Bancarias"),  # cargo por pago tardío, AMEX
    (r"PENALIZACION POR PAGO TARDIO", "Impuestos y Comisiones Bancarias"),  # BBVA TDC, mismo concepto
    # Fila sintética que arma parsers/bbva_credit.py con Monto de intereses
    # + comisiones + IVA cuando ese cargo nunca aparece como su propio
    # renglón en la tabla de movimientos (confirmado: a diferencia de la
    # penalización por pago tardío, el interés ordinario por revolvencia no
    # se itemiza) — ver el docstring del parser.
    (r"^Intereses y comisiones del periodo$", "Impuestos y Comisiones Bancarias"),
    # Viajes
    (r"\bMELIA\b", "Viajes"),  # cadena hotelera Meliá
    (r"SNOW\.COM/VAIL RESORTS", "Viajes"),
    # Compras
    (r"\bMUJI\b", "Compras"),
    (r"COLUMBIA BRANDS USA", "Compras"),  # marca de ropa outdoor
    (r"\bTARGET\b", "Compras"),
    (r"BILLABONG", "Compras"),  # marca de ropa surf
    (r"JOYERIA TRESSOR", "Compras"),
    (r"LPO MERCHANDISING", "Compras"),
    # Alimentos y Supermercado
    (r"BEVERAGES & MORE", "Alimentos y Supermercado"),
    (r"ULTRAMARINOS DE FRAN", "Alimentos y Supermercado"),
    # Cuidado Personal
    (r"KOTI WELLNESS", "Cuidado Personal"),
    # Cuarto lote: comercios de menor volumen individual pero identificables
    # sin ambigüedad razonable.
    (r"OPENAI \*CHATGPT SUBSCR", "Servicios y Suscripciones"),
    (r"OPENPAY\*DIDIFOODMX", "Delivery"),
    (r"REVES PADEL", "Entretenimiento"),
    (r"FIRE \+ ICE", "Restaurantes y Café"),  # South Lake Tahoe, viaje de esquí
    (r"LA DOCENA ROMA", "Restaurantes y Café"),  # ostionería, CDMX
    (r"\bMEROMA\b", "Restaurantes y Café"),  # CDMX
    (r"\bOSAKA\b", "Restaurantes y Café"),  # José Ignacio, Uruguay
    (r"LA BARRA DE FRAN", "Restaurantes y Café"),
    (r"IKANO RETAIL MEXICO", "Compras"),  # IKEA México
    (r"SIERRA #0096", "Compras"),  # outlet, Silverthorne
    (r"SAMS VENTA EN LINEA", "Alimentos y Supermercado"),
    # Quinto lote: identificados directamente por el usuario, no por
    # inferencia del nombre del comercio.
    (r"\bALMA MEXICO\b", "Entretenimiento"),  # antro, confirmado por el usuario
    (r"BILLPOCKET\*ALMA DANZOLO", "Entretenimiento"),  # mismo antro (Alma), sucursal/evento en Metepec
    (r"QOYA SP SAO PAULO", "Viajes"),  # hotel, confirmado por el usuario
    (r"ZEPIKA", "Regalos"),  # plataforma de regalos de boda, confirmado por el usuario — cubre OPENPAY* y las dos variantes de PAYPAL*
    (r"DEPORPRIVE", "Compras"),  # tienda, confirmado por el usuario — cubre el cargo directo y PAYPAL*
    (r"HULE CIUDAD DE MEX", "Restaurantes y Café"),  # bar, confirmado por el usuario
    (r"WALMART VENTA EN LINEA", "Alimentos y Supermercado"),  # confirmado por el usuario
    # Compra de producto Coca-Cola (el usuario trabaja ahí, confirmó que
    # probablemente son compras de producto a la propia empresa) — variantes
    # vistas en CDMX, Atlanta y Las Vegas. Distinto del SPEI RECIBIDOBANAMEX
    # "THE COCA COLA EXPORT CORPORATI[ON]" (ver KNOWN_PERSON_RULES-style
    # abajo): ese es dinero que regresa, no una compra, y por eso va a
    # Reembolsos y no aquí.
    (r"COCA COLA EXP CIB|COCA COLA EXPORT MEXICO|COCA COLA AOC|COCA COLA ATLANTA|COCA COLA LAS VEGAS", "Alimentos y Supermercado"),
    # SPEI recibidos de "THE COCA COLA EXPORT CORPORATI[ON]" vía "SERVICIOS
    # INTEG DE ADMINIS Y ALTA[GERENCIA]" (la misma procesadora que vendió los
    # boletos del Mundial, ver WORLD_CUP_PATTERNS) — un reembolso de gasto,
    # no ingreso ni compra. "CORPORATI" no colisiona con las variantes de
    # arriba (EXP CIB / EXPORT MEXICO / AOC / ATLANTA / LAS VEGAS).
    (r"COCA COLA EXPORT CORPORATI", "Reembolsos"),
    (r"DINSMOOR MEXICO", "Entretenimiento"),  # antro, confirmado por el usuario
    (r"\bAZIA MEXICO\b", "Restaurantes y Café"),  # restaurante, confirmado por el usuario
    # El usuario no recordaba el nombre del comercio pero sí que era el
    # antro "Draggaret" — confirmación de baja certeza ("me suena a que"),
    # documentada por si aparece con otro nombre de comercio más adelante.
    (r"BILLPOCKET\*D I S C O T", "Entretenimiento"),  # antro (Draggaret), Pachuca
    (r"MARIAJ LOS CABOS", "Restaurantes y Café"),  # restaurante, confirmado por el usuario — comido durante un viaje a Los Cabos
    # Sexto lote: identificados por búsqueda web (nombre de comercio + ciudad
    # cruzado contra directorios de restaurantes/negocios reales), a pedido
    # del usuario. Solo se incluyen los que la búsqueda confirmó con
    # razonable certeza — varios nombres genéricos (Porter México, Altavista,
    # Nala, SP Dossier, Sala Virgen, AYF Antara, La Isla Punta del Este, CE
    # Queretaro Jurica) quedaron fuera por tener múltiples negocios reales
    # con el mismo nombre sin forma de distinguir cuál es.
    (r"BILLPOCKET\*FUGU", "Restaurantes y Café"),  # sushi, CDMX
    (r"PAROLE CDMX", "Restaurantes y Café"),  # italiano, Polanco
    (r"FRESKO AVANDARO", "Alimentos y Supermercado"),  # tienda/abarrotes, Valle de Bravo — NO restaurante
    (r"SUPPLI II", "Restaurantes y Café"),  # trattoria italiana, Colonia Juárez
    (r"TIENDA LIVU", "Regalos"),  # plataforma de regalos (créditos/regalo), mismo perfil que Zepika
    (r"CASA OLIMPIA RES", "Restaurantes y Café"),  # mediterráneo, Polanco
    (r"BASEBYRENGMZ", "Cuidado Personal"),  # membresía de app de fitness (Base by Ren)
    (r"SENS ARCOS MEXICO", "Entretenimiento"),  # antro, Arcos Bosques
    (r"DASAHIPICO", "Entretenimiento"),  # club hípico, Valle de Bravo
    (r"ROCA CIUDAD DE MEX", "Restaurantes y Café"),  # Roca Ostrería y Parrilla, Lomas de Chapultepec
    (r"OPERADORA DOLCE TANGO", "Restaurantes y Café"),  # restaurantera, Condesa
    (r"BILLPOCKET\*CENTANNI", "Restaurantes y Café"),  # italiano, San Miguel de Allende
    (r"BILLPOCKET\*CARA DE VACA", "Restaurantes y Café"),  # parrilla norteña, San Pedro Garza García
    (r"BUHO GALLO", "Restaurantes y Café"),  # Gallobúho, Madrid
    (r"DISCOLAB XO SAS", "Restaurantes y Café"),  # bar/restaurante, Cartagena
    (r"MERCADOPAGO\*SEVENVINTAG", "Compras"),  # ropa vintage/segunda mano, CDMX
    (r"MERCADOPAGO\*CHATEAUPOLA", "Entretenimiento"),  # Chateau Piano Bar, Polanco
    (r"SRPAGO\*BUDAMAR", "Viajes"),  # hotel de playa, Zipolite
    (r"NETPAY\*EL LUCERO", "Restaurantes y Café"),  # cantina, Mérida
    (r"NETPAY\*YORU", "Restaurantes y Café"),  # sushi, Roma Norte
    (r"ESPACO RUBRO NEGRO", "Compras"),  # tienda oficial del Flamengo, Rio de Janeiro
]


def _first_match(text: str, rules: list[tuple[str, str]]) -> str | None:
    for pattern, category_name in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return category_name
    return None


def classify_merchants(session: Session) -> int:
    """Applies SELF_PAYMENT, investment-institution, income, and merchant
    rules, in that order, to every still-uncategorized transaction.
    Idempotent — only touches category_id is None, so it never overwrites a
    category classify_transfers (or a human) already assigned."""
    categories = {c.name: c for c in session.query(Category).all()}

    matched = 0
    for txn in session.query(Transaction).filter(Transaction.category_id.is_(None)):
        haystack = f"{txn.raw_description or ''} {txn.description}"

        category_name = _first_match(haystack, SELF_PAYMENT_RULES)
        if category_name is None and any(
            re.search(p, haystack, re.IGNORECASE) for p in WORLD_CUP_PATTERNS
        ):
            category_name = _WORLD_CUP_CATEGORY
        if category_name is None and any(
            re.search(p, haystack, re.IGNORECASE) for p in INVESTMENT_INSTITUTION_PATTERNS
        ):
            category_name = _INVESTMENT_CATEGORY
        if category_name is None:
            category_name = _first_match(haystack, INCOME_RULES)
        # Va antes que las reglas por nombre para que, dentro de su
        # vigencia, la relación gane. Fuera de vigencia el movimiento sigue
        # cayendo a las reglas genéricas de abajo: si el memo dice "renta"
        # se irá a Vivienda de todos modos, pero por lo que dice el propio
        # movimiento y no por quién es la contraparte — que es justo la
        # distinción que se quería.
        if category_name is None:
            category_name = _world_cup_memo_match(haystack, txn.date, txn.amount)
        if category_name is None:
            category_name = _colombia_trip_match(haystack, txn.amount)
        if category_name is None:
            category_name = _roommate_rent_match(haystack, txn.date)
        if category_name is None:
            category_name = _first_dated_match(haystack, txn.date)
        if category_name is None:
            category_name = _first_match(haystack, KNOWN_PERSON_RULES)
        if category_name is None and any(
            re.search(p, haystack, re.IGNORECASE) for p in RENT_PATTERNS
        ):
            category_name = _RENT_CATEGORY
        if category_name is None:
            category_name = _first_match(haystack, MERCHANT_RULES)

        if category_name is None:
            continue
        category = categories.get(category_name)
        if category is None:
            continue
        txn.category_id = category.id
        matched += 1

    session.commit()
    return matched


def classify_spend_frequency(session: Session) -> int:
    """Tags fijo/variable on every still-untagged transaction that's a real
    gasto: category.kind == EXPENSE, or uncategorized with amount < 0 (same
    "counts as spend until proven otherwise" convention /spending-by-category
    uses for 'Sin categoría'). Income and transfers are left untagged — the
    dimension doesn't apply to them. Idempotent: only touches
    spend_frequency is None, so it never overwrites a prior run or a manual
    correction. Run after classify_merchants, since it relies on category
    already being set where possible."""
    tagged = 0
    query = (
        session.query(Transaction)
        .outerjoin(Category)
        .filter(
            Transaction.spend_frequency.is_(None),
            ((Category.kind == CategoryKind.EXPENSE) | ((Transaction.category_id.is_(None)) & (Transaction.amount < 0))),
        )
    )
    for txn in query:
        haystack = f"{txn.raw_description or ''} {txn.description}"
        is_fixed = any(re.search(p, haystack, re.IGNORECASE) for p in FIXED_EXPENSE_PATTERNS)
        txn.spend_frequency = SpendFrequency.FIJO if is_fixed else SpendFrequency.VARIABLE
        tagged += 1

    session.commit()
    return tagged
