"""Regenerates the Excel mirror of the dashboard from the live database.

Run monthly, after new statements are imported and classified:
    cd backend && python3 -m scripts.export_excel [output_path]

Why this exists: the live FastAPI+React dashboard needs a host with a
persistent disk (Fly.io), which turned out to be unreachable without a
terminal (see DEPLOY.md). This script produces a self-contained .xlsx
instead, regenerated on demand rather than continuously hosted. It reuses
the exact same business logic as the API (imports the live functions from
app.main and app.savings_goal, not a reimplementation), so the numbers are
guaranteed to match what the dashboard would have shown.

Design: sheets that are simple aggregates over the transaction ledger
(Resumen Mensual, Gasto por Categoría, the per-month table in Meta de
Ahorro) are built as Excel formulas (SUMIFS) against the Transacciones
sheet, so they're auditable and recalculate if a cell is edited. Sheets
whose numbers come from real one-off business rules that don't map to a
maintainable spreadsheet formula (Patrimonio's per-account exclusions,
Meta de Ahorro's streak/projection, which depends on "today" and a
trailing-12-month median) are written as plain computed values, clearly
labeled as a snapshot refreshed by re-running this script — not something
meant to be hand-edited.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from app.db import get_session
from app.main import net_worth, savings_goal, savings_projection, _compute_monthly_summary
from app.models import Account, Category, CategoryKind, Transaction
from scripts.cache_values import inject_cached_values

FONT_NAME = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(name=FONT_NAME, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=FONT_NAME, bold=True, size=14)
NOTE_FONT = Font(name=FONT_NAME, italic=True, size=9, color="666666")
BODY_FONT = Font(name=FONT_NAME)
BOLD_FONT = Font(name=FONT_NAME, bold=True)
MONEY_FMT = "$#,##0.00;($#,##0.00);-"
PCT_FMT = "0.0%"

TIPO_BY_KIND = {"income": "INGRESO", "expense": "GASTO", "transfer": "TRANSFERENCIA"}
NATURE_LABEL = {"basico": "Básico", "necesario": "Necesario", "estilo_de_vida": "Estilo de vida"}


def style_header_row(ws: Worksheet, row: int, ncols: int, start_col: int = 1) -> None:
    for col in range(start_col, start_col + ncols):
        cell = ws.cell(row=row, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def autosize(ws: Worksheet, widths: dict[str, int]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[col].width = width


def build_leeme(wb: Workbook, generated_at: datetime.datetime, nw: dict) -> None:
    ws = wb.active
    ws.title = "Léeme"
    ws["A1"] = "Finanzas personales — Gonzalo Rodríguez Fierro"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Generado: {generated_at.strftime('%Y-%m-%d %H:%M')}"
    ws["A2"].font = NOTE_FONT

    lines = [
        "",
        "Cómo funciona este archivo",
        "Este workbook se regenera completo cada vez que se corre "
        "backend/scripts/export_excel.py contra la base de datos real "
        "(backend/finanzas.db) — no se edita a mano. El flujo mensual: subes "
        "los estados de cuenta nuevos a Drive, se lo dices a Claude, Claude "
        "los importa/clasifica en la base de datos (misma lógica de siempre) "
        "y vuelve a correr este script para regenerar el archivo.",
        "",
        "Qué es cada hoja",
        "- Dashboard: KPIs y las 6 gráficas (Flujo de Efectivo, Histórico de "
        "Ingreso, Histórico de Gasto, Gasto por Categoría con selector de "
        "mes, y las 2 de Proyección de Ahorro). Es la vista principal.",
        "- Transacciones: el libro mayor completo, una fila por movimiento.",
        "- Patrimonio: balance por cuenta y totales por moneda (igual que "
        "/net-worth del dashboard).",
        "- Resumen Mensual: ingreso/gasto/neto por mes en MXN (fórmulas "
        "SUMIFS contra Transacciones — auditable).",
        "- Gasto por Categoría: matriz categoría × mes en MXN (fórmulas).",
        "- Meta de Ahorro: seguimiento de la meta de 15% + proyección a "
        "2026-2028.",
        "",
        "Fórmulas vs. valores calculados",
        "Resumen Mensual, Gasto por Categoría y la tabla mes-a-mes de Meta "
        "de Ahorro usan fórmulas de Excel (SUMIFS) contra la hoja "
        "Transacciones — si algo se ve raro ahí, se puede auditar celda por "
        "celda. Patrimonio y los bloques de racha/proyección en Meta de "
        "Ahorro son valores calculados por Python al momento de generar el "
        "archivo (misma lógica que el API), marcados como tal — dependen de "
        "reglas específicas por cuenta (ej. GBM, Balagan) y de la fecha de "
        "hoy, así que no son fórmulas vivas: se actualizan re-corriendo el "
        "script, no abriendo el archivo.",
        "",
        "Solo MXN en Resumen Mensual / Gasto por Categoría / Meta de Ahorro",
        "Las cuentas en USD/EUR (Bitso, Shareworks, Grupo Arreola) no tienen "
        "movimientos clasificados como ingreso/gasto — son inversión o "
        "balance, no flujo de efectivo — así que esas hojas replican "
        "exactamente lo que el dashboard mostraría en MXN, su vista por "
        "default.",
        "",
        "",
        "Dashboard: columnas AF en adelante son datos de soporte para las "
        "gráficas (no para leer directamente) — la celda junto a 'Mes "
        "seleccionado:' sí es un selector: cambia el mes ahí y la gráfica "
        "de Gasto por Categoría se actualiza sola.",
        "",
        "Convención de signo: negativo = sale (cargo/gasto), positivo = "
        "entra (abono/ingreso). Un traspaso entre cuentas propias (ej. "
        "BBVA → GBM) no es ni gasto ni ingreso, categoría "
        "'Transferencia entre Cuentas Propias'.",
        "",
        "Notas del cálculo de patrimonio (de /net-worth):",
    ]
    section_headers = {
        "Cómo funciona este archivo", "Qué es cada hoja", "Fórmulas vs. valores calculados",
        "Solo MXN en Resumen Mensual / Gasto por Categoría / Meta de Ahorro",
        "Notas del cálculo de patrimonio (de /net-worth):",
    }
    row = 4
    for line in lines:
        cell = ws.cell(row=row, column=1, value=line)
        cell.font = BOLD_FONT if line in section_headers else BODY_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    for caveat in nw["caveats"]:
        cell = ws.cell(row=row, column=1, value=f"• {caveat}")
        cell.font = BODY_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    ws.column_dimensions["A"].width = 110


def build_transacciones(wb: Workbook, session) -> int:
    ws = wb.create_sheet("Transacciones")
    headers = [
        "Fecha", "Mes", "Cuenta", "Institución", "Moneda", "Monto",
        "Descripción", "Categoría", "Tipo", "Naturaleza", "Frecuencia", "Archivo Fuente",
    ]
    ws.append(headers)
    style_header_row(ws, 1, len(headers))

    rows = (
        session.query(Transaction, Account, Category)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .order_by(Transaction.date, Transaction.id)
        .all()
    )

    r = 2
    for t, a, c in rows:
        tipo = TIPO_BY_KIND.get(c.kind.value) if c else ""
        naturaleza = NATURE_LABEL.get(c.nature.value) if c and c.nature else ""
        frecuencia = t.spend_frequency.value.capitalize() if t.spend_frequency else ""
        ws.append([
            t.date, t.date.strftime("%Y-%m"), a.name, a.institution, t.currency,
            t.amount, t.description, c.name if c else "", tipo, naturaleza, frecuencia,
            t.source_file or "",
        ])
        ws.cell(row=r, column=1).number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=6).number_format = MONEY_FMT
        for col in range(1, len(headers) + 1):
            ws.cell(row=r, column=col).font = BODY_FONT
        r += 1

    last_row = r - 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last_row}"
    autosize(ws, {
        "A": 12, "B": 9, "C": 16, "D": 16, "E": 8, "F": 14,
        "G": 46, "H": 26, "I": 14, "J": 14, "K": 11, "L": 34,
    })
    return last_row


def build_patrimonio(wb: Workbook, nw: dict) -> dict:
    ws = wb.create_sheet("Patrimonio")
    ws["A1"] = "Patrimonio"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Valores calculados por Python al generar el archivo (misma lógica que /net-worth) — no son fórmulas."
    ws["A2"].font = NOTE_FONT

    headers = ["Cuenta", "Tipo", "Moneda", "Balance", "Excluido del total", "Notas"]
    header_row = 4
    for i, h in enumerate(headers, start=1):
        ws.cell(row=header_row, column=i, value=h)
    style_header_row(ws, header_row, len(headers))

    r = header_row + 1
    first_data_row = r
    for acc in nw["by_account"]:
        ws.cell(row=r, column=1, value=acc["account"]).font = BODY_FONT
        ws.cell(row=r, column=2, value=acc["kind"]).font = BODY_FONT
        ws.cell(row=r, column=3, value=acc["currency"]).font = BODY_FONT
        bal_cell = ws.cell(row=r, column=4, value=acc["balance"])
        bal_cell.number_format = MONEY_FMT
        bal_cell.font = BODY_FONT
        ws.cell(row=r, column=5, value="Sí" if acc.get("excluded_from_total") else "No").font = BODY_FONT
        detail = acc.get("detail")
        notes = "; ".join(f"{k}={v}" for k, v in detail.items()) if detail else ""
        ws.cell(row=r, column=6, value=notes).font = BODY_FONT
        r += 1
    last_data_row = r - 1

    r += 1
    ws.cell(row=r, column=1, value="Total por moneda").font = BOLD_FONT
    r += 1
    ws.cell(row=r, column=1, value="Moneda").font = HEADER_FONT
    ws.cell(row=r, column=2, value="Total").font = HEADER_FONT
    for cell in (ws.cell(row=r, column=1), ws.cell(row=r, column=2)):
        cell.fill = HEADER_FILL
    r += 1
    currency_row: dict[str, int] = {}
    for currency, total in nw["total_by_currency"].items():
        ws.cell(row=r, column=1, value=currency).font = BODY_FONT
        formula = (
            f"=SUMIFS(D{first_data_row}:D{last_data_row},"
            f"C{first_data_row}:C{last_data_row},A{r},"
            f"E{first_data_row}:E{last_data_row},\"No\")"
        )
        cell = ws.cell(row=r, column=2, value=formula)
        cell.number_format = MONEY_FMT
        cell.font = BOLD_FONT
        currency_row[currency] = r
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Notas / caveats").font = BOLD_FONT
    r += 1
    for caveat in nw["caveats"]:
        cell = ws.cell(row=r, column=1, value=f"• {caveat}")
        cell.font = NOTE_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        r += 1

    autosize(ws, {"A": 22, "B": 20, "C": 9, "D": 16, "E": 16, "F": 60})
    return {"currency_row": currency_row}


def build_resumen_mensual(wb: Workbook, months: list[dict], txn_last_row: int) -> dict:
    ws = wb.create_sheet("Resumen Mensual")
    ws["A1"] = "Resumen Mensual (MXN)"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Fórmulas SUMIFS contra la hoja Transacciones — recalcula si Transacciones cambia."
    ws["A2"].font = NOTE_FONT

    headers = ["Mes", "Ingreso", "Gasto Total", "Neto", "Gasto Fijo", "Gasto Variable"]
    header_row = 4
    for i, h in enumerate(headers, start=1):
        ws.cell(row=header_row, column=i, value=h)
    style_header_row(ws, header_row, len(headers))

    T = "Transacciones!"
    mes_col, moneda_col, monto_col, cat_col, tipo_col, freq_col = "B", "E", "F", "H", "I", "K"
    rng = lambda col: f"{T}${col}$2:${col}${txn_last_row}"

    def sumifs(*criteria: str) -> str:
        """criteria are (range_col, value) pairs, already-quoted where needed."""
        parts = [rng(monto_col)]
        for col, value in zip(criteria[0::2], criteria[1::2]):
            parts.append(rng(col))
            parts.append(value)
        return f"SUMIFS({','.join(parts)})"

    r = header_row + 1
    for m in months:
        month = m["month"]
        ws.cell(row=r, column=1, value=month).font = BODY_FONT

        ingreso_expr = sumifs(mes_col, f"A{r}", moneda_col, '"MXN"', tipo_col, '"INGRESO"')
        gasto_cat_expr = sumifs(mes_col, f"A{r}", moneda_col, '"MXN"', tipo_col, '"GASTO"')
        gasto_sin_cat_expr = sumifs(mes_col, f"A{r}", moneda_col, '"MXN"', cat_col, '""', monto_col, '"<0"')
        fijo_expr = sumifs(mes_col, f"A{r}", moneda_col, '"MXN"', freq_col, '"Fijo"')
        variable_expr = sumifs(mes_col, f"A{r}", moneda_col, '"MXN"', freq_col, '"Variable"')

        ws.cell(row=r, column=2, value=f"={ingreso_expr}")
        ws.cell(row=r, column=3, value=f"=-{gasto_cat_expr}-{gasto_sin_cat_expr}")
        ws.cell(row=r, column=4, value=f"=B{r}-C{r}")
        ws.cell(row=r, column=5, value=f"=-{fijo_expr}")
        ws.cell(row=r, column=6, value=f"=-{variable_expr}")
        for col in (2, 3, 4, 5, 6):
            c = ws.cell(row=r, column=col)
            c.number_format = MONEY_FMT
            c.font = BODY_FONT
        r += 1

    last_data_row = r - 1
    ws.freeze_panes = f"A{header_row + 1}"
    autosize(ws, {"A": 10, "B": 16, "C": 16, "D": 16, "E": 16, "F": 16})
    return {"header_row": header_row, "first_data_row": header_row + 1, "last_data_row": last_data_row}


def build_gasto_por_categoria(wb: Workbook, months: list[dict], categories: list[Category], txn_last_row: int) -> dict:
    ws = wb.create_sheet("Gasto por Categoría")
    ws["A1"] = "Gasto por Categoría (MXN)"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Fórmulas SUMIFS contra Transacciones. Fila 'Sin categoría' solo cuenta cargos (monto<0), igual que el dashboard."
    ws["A2"].font = NOTE_FONT

    month_list = [m["month"] for m in months]
    header_row = 4
    ws.cell(row=header_row, column=1, value="Categoría")
    ws.cell(row=header_row, column=2, value="Naturaleza")
    for j, month in enumerate(month_list, start=3):
        ws.cell(row=header_row, column=j, value=month)
    total_col = 3 + len(month_list)
    ws.cell(row=header_row, column=total_col, value="Total")
    style_header_row(ws, header_row, total_col)

    T = "Transacciones!"
    mes_col, moneda_col, monto_col, cat_col = "B", "E", "F", "H"
    rng = lambda col: f"{T}${col}$2:${col}${txn_last_row}"

    expense_categories = categories
    r = header_row + 1
    first_data_row = r
    for cat in expense_categories:
        ws.cell(row=r, column=1, value=cat.name).font = BODY_FONT
        ws.cell(row=r, column=2, value=NATURE_LABEL.get(cat.nature.value, "") if cat.nature else "").font = BODY_FONT
        for j, month in enumerate(month_list, start=3):
            col_letter = get_column_letter(j)
            formula = (
                f'=-SUMIFS({rng(monto_col)},{rng(mes_col)},{col_letter}${header_row},'
                f'{rng(moneda_col)},"MXN",{rng(cat_col)},$A{r})'
            )
            cell = ws.cell(row=r, column=j, value=formula)
            cell.number_format = MONEY_FMT
        r += 1

    ws.cell(row=r, column=1, value="Sin categoría").font = BODY_FONT
    for j, month in enumerate(month_list, start=3):
        col_letter = get_column_letter(j)
        formula = (
            f'=-SUMIFS({rng(monto_col)},{rng(mes_col)},{col_letter}${header_row},'
            f'{rng(moneda_col)},"MXN",{rng(cat_col)},"",{rng(monto_col)},"<0")'
        )
        cell = ws.cell(row=r, column=j, value=formula)
        cell.number_format = MONEY_FMT
    last_data_row = r

    # Total column (per-row) and total row (per-column), both plain SUM
    # over this sheet's own grid — cheap cross-check against Resumen Mensual.
    for row in range(first_data_row, last_data_row + 1):
        first_month_col = get_column_letter(3)
        last_month_col = get_column_letter(total_col - 1)
        cell = ws.cell(row=row, column=total_col, value=f"=SUM({first_month_col}{row}:{last_month_col}{row})")
        cell.number_format = MONEY_FMT
        cell.font = BOLD_FONT

    total_row = last_data_row + 2
    ws.cell(row=total_row, column=1, value="Total").font = BOLD_FONT
    for j in range(3, total_col + 1):
        col_letter = get_column_letter(j)
        cell = ws.cell(row=total_row, column=j, value=f"=SUM({col_letter}{first_data_row}:{col_letter}{last_data_row})")
        cell.number_format = MONEY_FMT
        cell.font = BOLD_FONT

    ws.freeze_panes = ws.cell(row=header_row + 1, column=3).coordinate
    widths = {"A": 30, "B": 14}
    for j in range(3, total_col + 1):
        widths[get_column_letter(j)] = 12
    autosize(ws, widths)
    return {
        "header_row": header_row,
        "first_data_row": first_data_row,
        "last_data_row": last_data_row,
        "total_col": total_col,
        "month_list": month_list,
    }


def build_meta_de_ahorro(wb: Workbook, goal: dict, projection: dict) -> None:
    ws = wb.create_sheet("Meta de Ahorro")
    ws["A1"] = "Meta de Ahorro"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Meta: {goal['target_pct']}% del ingreso mensual. Tabla mes-a-mes por fórmula; resumen/proyección son valores calculados (ver Léeme)."
    ws["A2"].font = NOTE_FONT

    headers = ["Mes", "Ingreso", "Neto", "Meta ($)", "Cumplida"]
    header_row = 4
    for i, h in enumerate(headers, start=1):
        ws.cell(row=header_row, column=i, value=h)
    style_header_row(ws, header_row, len(headers))

    r = header_row + 1
    for i, m in enumerate(goal["months"]):
        rm_row = i + 5  # Resumen Mensual: header at row 4, data starts row 5
        ws.cell(row=r, column=1, value=m["month"]).font = BODY_FONT
        ws.cell(row=r, column=2, value=f"='Resumen Mensual'!B{rm_row}")
        ws.cell(row=r, column=3, value=f"='Resumen Mensual'!D{rm_row}")
        ws.cell(row=r, column=4, value=f"=IF(B{r}>0,B{r}*{goal['target_pct']}/100,0)")
        ws.cell(row=r, column=5, value=f'=IF(B{r}<=0,"—",IF(C{r}>=D{r},"Sí","No"))')
        for col in (2, 3, 4):
            c = ws.cell(row=r, column=col)
            c.number_format = MONEY_FMT
            c.font = BODY_FONT
        ws.cell(row=r, column=5).font = BODY_FONT
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Resumen (calculado)").font = BOLD_FONT
    r += 1
    summary = goal["summary"]
    summary_rows = [
        ("Meses evaluados", summary["months_evaluated"]),
        ("Meses cumplidos", summary["months_met"]),
        ("Racha actual (meses consecutivos)", summary["current_streak"]),
        ("Tasa de ahorro histórica", summary["historical_savings_rate_pct"] / 100),
    ]
    for label, value in summary_rows:
        ws.cell(row=r, column=1, value=label).font = BODY_FONT
        cell = ws.cell(row=r, column=2, value=value)
        if "Tasa" in label:
            cell.number_format = PCT_FMT
        cell.font = BODY_FONT
        r += 1

    r += 1
    ws.cell(row=r, column=1, value=f"Proyección (instantánea al {projection['as_of']}, calculada)").font = BOLD_FONT
    r += 1
    ws.cell(row=r, column=1, value="Ingreso mensual mediano (últimos 12 meses cerrados)").font = BODY_FONT
    c = ws.cell(row=r, column=2, value=projection["median_monthly_income"])
    c.number_format = MONEY_FMT
    r += 1
    ws.cell(row=r, column=1, value="Gasto fijo mensual mediano").font = BODY_FONT
    c = ws.cell(row=r, column=2, value=projection["median_monthly_fijo"])
    c.number_format = MONEY_FMT
    r += 1
    ws.cell(row=r, column=1, value="Patrimonio actual (MXN)").font = BODY_FONT
    c = ws.cell(row=r, column=2, value=projection["current_net_worth_mxn"])
    c.number_format = MONEY_FMT
    r += 2

    scen_headers = ["Escenario", "Tasa", "Ahorro mensual", "Presupuesto variable mensual", "Año", "Meses restantes", "Ahorro acumulado", "Patrimonio proyectado"]
    for i, h in enumerate(scen_headers, start=1):
        ws.cell(row=r, column=i, value=h)
    style_header_row(ws, r, len(scen_headers))
    r += 1
    for scenario in projection["scenarios"]:
        first_row_for_scenario = r
        for milestone in scenario["milestones"]:
            ws.cell(row=r, column=1, value=scenario["label"]).font = BODY_FONT
            rate_cell = ws.cell(row=r, column=2, value=scenario["rate_pct"] / 100)
            rate_cell.number_format = PCT_FMT
            for col, val in ((3, scenario["monthly_savings"]), (4, scenario["monthly_variable_budget"])):
                c = ws.cell(row=r, column=col, value=val)
                c.number_format = MONEY_FMT
                c.font = BODY_FONT
            ws.cell(row=r, column=5, value=milestone["year"]).font = BODY_FONT
            ws.cell(row=r, column=6, value=milestone["months_remaining"]).font = BODY_FONT
            for col, val in ((7, milestone["cumulative_savings"]), (8, milestone["projected_net_worth_mxn"])):
                c = ws.cell(row=r, column=col, value=val)
                c.number_format = MONEY_FMT
                c.font = BODY_FONT
            r += 1

    r += 1
    for caveat in projection["caveats"]:
        cell = ws.cell(row=r, column=1, value=f"• {caveat}")
        cell.font = NOTE_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 1

    autosize(ws, {"A": 24, "B": 14, "C": 16, "D": 20, "E": 24, "F": 10, "G": 16, "H": 20})


CHART_W, CHART_H = 20, 10
# Two chart columns (left/right) each ~12 default-width columns wide at
# CHART_W=20cm (~1.7cm/column) — RIGHT_COL leaves a buffer past LEFT_COL's
# span, and HELPER_COL starts well past RIGHT_COL's own span so the chart-
# feeding helper tables never render underneath a chart image.
LEFT_COL, RIGHT_COL, HELPER_COL = 2, 17, 32  # B, Q, AF
SCEN_LABEL = {"minimo": "Mínimo (15%)", "ambicioso": "Ambicioso (30%)"}


def _sized(chart, title: str):
    chart.title = title
    chart.width = CHART_W
    chart.height = CHART_H
    chart.style = 10
    return chart


def build_dashboard(
    wb: Workbook,
    resumen_meta: dict,
    gasto_meta: dict,
    patrimonio_meta: dict,
    categories: list[Category],
    projection: dict,
) -> None:
    """All 6 charts are built from cell references (Resumen Mensual /
    Gasto por Categoría / small helper tables on this sheet), never from
    Python values baked into the chart directly — so they redraw correctly
    if this sheet is regenerated with more months of data. The one
    exception is the two projection charts: their helper tables are plain
    values (same reasoning as Meta de Ahorro's own projection block — see
    that sheet's note — it's a dated snapshot, not something derivable by
    formula from the ledger)."""
    ws = wb.create_sheet("Dashboard")
    ws["A1"] = "Dashboard — Finanzas"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (
        "KPIs y gráficas de Flujo de Efectivo / Históricos / Gasto por Categoría se recalculan solas si "
        "Resumen Mensual o Gasto por Categoría cambian. Las 2 de Proyección de Ahorro son una instantánea "
        "(ver Meta de Ahorro)."
    )
    ws["A2"].font = NOTE_FONT

    rm, gm = resumen_meta, gasto_meta
    last_rm_row = rm["last_data_row"]
    ws_rm = wb["Resumen Mensual"]
    ws_gm = wb["Gasto por Categoría"]

    # ---- KPI tiles ----
    tile_label_row, tile_value_row = 4, 5
    mxn_row = patrimonio_meta["currency_row"].get("MXN")
    tiles = [
        ("Patrimonio Total (MXN)", f"='Patrimonio'!B{mxn_row}" if mxn_row else '="—"'),
        (f'="Ingreso — "&\'Resumen Mensual\'!A{last_rm_row}', f"='Resumen Mensual'!B{last_rm_row}"),
        (f'="Gasto — "&\'Resumen Mensual\'!A{last_rm_row}', f"='Resumen Mensual'!C{last_rm_row}"),
        (f'="Neto — "&\'Resumen Mensual\'!A{last_rm_row}', f"='Resumen Mensual'!D{last_rm_row}"),
    ]
    for i, (label_formula, value_formula) in enumerate(tiles):
        col_start = 2 + i * 4  # B, F, J, N
        col_end = col_start + 2
        c0, c1 = get_column_letter(col_start), get_column_letter(col_end)
        ws.merge_cells(f"{c0}{tile_label_row}:{c1}{tile_label_row}")
        lbl = ws.cell(row=tile_label_row, column=col_start, value=label_formula)
        lbl.font = Font(name=FONT_NAME, size=10, color="666666")
        ws.merge_cells(f"{c0}{tile_value_row}:{c1}{tile_value_row + 1}")
        val = ws.cell(row=tile_value_row, column=col_start, value=value_formula)
        val.font = Font(name=FONT_NAME, size=18, bold=True)
        val.number_format = MONEY_FMT
        val.alignment = Alignment(vertical="center")

    # ---- Chart 1: Flujo de Efectivo — histórico (bar Ingreso/Gasto + línea Neto) ----
    cats_all = Reference(ws_rm, min_col=1, min_row=rm["first_data_row"], max_row=last_rm_row)
    bar = _sized(BarChart(), "Flujo de Efectivo — Histórico")
    bar.type = "col"
    bar.y_axis.title = "MXN"
    bar.add_data(
        Reference(ws_rm, min_col=2, max_col=3, min_row=rm["header_row"], max_row=last_rm_row),
        titles_from_data=True,
    )
    bar.set_categories(cats_all)
    line = LineChart()
    line.add_data(
        Reference(ws_rm, min_col=4, min_row=rm["header_row"], max_row=last_rm_row), titles_from_data=True
    )
    line.set_categories(cats_all)
    bar += line
    ws.add_chart(bar, f"{get_column_letter(LEFT_COL)}9")

    # ---- Chart 2: Histórico de Ingreso ----
    income_chart = _sized(LineChart(), "Histórico de Ingreso")
    income_chart.y_axis.title = "MXN"
    income_chart.add_data(
        Reference(ws_rm, min_col=2, min_row=rm["header_row"], max_row=last_rm_row), titles_from_data=True
    )
    income_chart.set_categories(cats_all)
    ws.add_chart(income_chart, f"{get_column_letter(RIGHT_COL)}9")

    # ---- Chart 3: Histórico de Gasto (fijo + variable, apilado) ----
    expense_chart = _sized(BarChart(), "Histórico de Gasto (Fijo + Variable)")
    expense_chart.type = "col"
    expense_chart.grouping = "stacked"
    expense_chart.overlap = 100
    expense_chart.y_axis.title = "MXN"
    expense_chart.add_data(
        Reference(ws_rm, min_col=5, max_col=6, min_row=rm["header_row"], max_row=last_rm_row),
        titles_from_data=True,
    )
    expense_chart.set_categories(cats_all)
    ws.add_chart(expense_chart, f"{get_column_letter(LEFT_COL)}31")

    # ---- Chart 4: Gasto por Categoría — mes seleccionado (con dropdown) ----
    month_label_row = 30
    ws.cell(row=month_label_row, column=RIGHT_COL - 1, value="Mes seleccionado:").font = BOLD_FONT
    month_cell = f"{get_column_letter(RIGHT_COL)}{month_label_row}"
    ws[month_cell] = gm["month_list"][-1]
    dv = DataValidation(
        type="list",
        formula1=f"='Resumen Mensual'!$A${rm['first_data_row']}:$A${last_rm_row}",
        allow_blank=False,
    )
    ws.add_data_validation(dv)
    dv.add(ws[month_cell])

    helper_cat_col, helper_val_col = HELPER_COL, HELPER_COL + 1
    helper_cat_letter, helper_val_letter = get_column_letter(helper_cat_col), get_column_letter(helper_val_col)
    ws.cell(row=3, column=helper_cat_col, value="Categoría").font = HEADER_FONT
    ws.cell(row=3, column=helper_val_col, value="Monto").font = HEADER_FONT
    for cell in (ws.cell(row=3, column=helper_cat_col), ws.cell(row=3, column=helper_val_col)):
        cell.fill = HEADER_FILL
    cat_names = [c.name for c in categories] + ["Sin categoría"]
    first_month_col = get_column_letter(3)
    last_month_col = get_column_letter(gm["total_col"] - 1)
    for i, name in enumerate(cat_names):
        r = 4 + i
        ws.cell(row=r, column=helper_cat_col, value=name).font = BODY_FONT
        formula = (
            f"=INDEX('Gasto por Categoría'!${first_month_col}${gm['first_data_row']}:"
            f"${last_month_col}${gm['last_data_row']},"
            f"MATCH(${helper_cat_letter}{r},'Gasto por Categoría'!$A${gm['first_data_row']}:$A${gm['last_data_row']},0),"
            f"MATCH(${month_cell},'Gasto por Categoría'!${first_month_col}${gm['header_row']}:"
            f"${last_month_col}${gm['header_row']},0))"
        )
        ws.cell(row=r, column=helper_val_col, value=formula).number_format = MONEY_FMT
    helper_last_row = 4 + len(cat_names) - 1

    cat_chart = _sized(BarChart(), "Gasto por Categoría — mes seleccionado")
    cat_chart.type = "bar"
    cat_chart.x_axis.title = None
    cat_chart.add_data(
        Reference(ws, min_col=helper_val_col, min_row=3, max_row=helper_last_row), titles_from_data=True
    )
    cat_chart.set_categories(Reference(ws, min_col=helper_cat_col, min_row=4, max_row=helper_last_row))
    ws.add_chart(cat_chart, f"{get_column_letter(RIGHT_COL)}31")

    # ---- Charts 5-6: Proyección de Ahorro (instantánea, ver Meta de Ahorro) ----
    proj_start = helper_last_row + 3
    ws.cell(row=proj_start, column=helper_cat_col, value="Escenario")
    for j, h in enumerate(("Gasto Fijo", "Gasto Variable", "Ahorro"), start=1):
        ws.cell(row=proj_start, column=helper_cat_col + j, value=h)
    style_header_row(ws, proj_start, 4, start_col=helper_cat_col)
    r = proj_start + 1
    for scenario in projection["scenarios"]:
        ws.cell(row=r, column=helper_cat_col, value=SCEN_LABEL.get(scenario["label"], scenario["label"]))
        ws.cell(row=r, column=helper_cat_col + 1, value=projection["median_monthly_fijo"]).number_format = MONEY_FMT
        ws.cell(row=r, column=helper_cat_col + 2, value=scenario["monthly_variable_budget"]).number_format = MONEY_FMT
        ws.cell(row=r, column=helper_cat_col + 3, value=scenario["monthly_savings"]).number_format = MONEY_FMT
        r += 1
    proj_last = r - 1

    budget_chart = _sized(BarChart(), "Presupuesto Mensual Proyectado por Escenario")
    budget_chart.type = "col"
    budget_chart.grouping = "stacked"
    budget_chart.overlap = 100
    budget_chart.y_axis.title = "MXN / mes"
    budget_chart.add_data(
        Reference(ws, min_col=helper_cat_col + 1, max_col=helper_cat_col + 3, min_row=proj_start, max_row=proj_last),
        titles_from_data=True,
    )
    budget_chart.set_categories(Reference(ws, min_col=helper_cat_col, min_row=proj_start + 1, max_row=proj_last))
    ws.add_chart(budget_chart, f"{get_column_letter(LEFT_COL)}53")

    milestone_start = proj_last + 3
    ws.cell(row=milestone_start, column=helper_cat_col, value="Año")
    scenario_cols = {}
    for j, scenario in enumerate(projection["scenarios"], start=1):
        col = helper_cat_col + j
        scenario_cols[scenario["label"]] = col
        ws.cell(row=milestone_start, column=col, value=SCEN_LABEL.get(scenario["label"], scenario["label"]))
    style_header_row(ws, milestone_start, 1 + len(projection["scenarios"]), start_col=helper_cat_col)
    years = [m["year"] for m in projection["scenarios"][0]["milestones"]]
    for i, year in enumerate(years):
        r = milestone_start + 1 + i
        ws.cell(row=r, column=helper_cat_col, value=str(year)).font = BODY_FONT
        for scenario in projection["scenarios"]:
            val = scenario["milestones"][i]["cumulative_savings"]
            ws.cell(row=r, column=scenario_cols[scenario["label"]], value=val).number_format = MONEY_FMT
    milestone_last = milestone_start + len(years)

    milestone_chart = _sized(BarChart(), "Ahorro Acumulado Proyectado")
    milestone_chart.type = "col"
    milestone_chart.grouping = "clustered"
    milestone_chart.y_axis.title = "MXN acumulados"
    milestone_chart.add_data(
        Reference(
            ws, min_col=helper_cat_col + 1, max_col=helper_cat_col + len(projection["scenarios"]),
            min_row=milestone_start, max_row=milestone_last,
        ),
        titles_from_data=True,
    )
    milestone_chart.set_categories(Reference(ws, min_col=helper_cat_col, min_row=milestone_start + 1, max_row=milestone_last))
    ws.add_chart(milestone_chart, f"{get_column_letter(RIGHT_COL)}53")

    ws.column_dimensions["A"].width = 4


def main() -> None:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent.parent / "Finanzas.xlsx"
    generated_at = datetime.datetime.now()

    nw = net_worth()
    goal = savings_goal(currency="MXN")
    projection = savings_projection(currency="MXN")

    with get_session() as session:
        months = _compute_monthly_summary(session, "MXN")
        categories = (
            session.query(Category)
            .filter(Category.kind == CategoryKind.EXPENSE)
            .order_by(Category.name)
            .all()
        )

        wb = Workbook()
        build_leeme(wb, generated_at, nw)
        txn_last_row = build_transacciones(wb, session)
        patrimonio_meta = build_patrimonio(wb, nw)
        resumen_meta = build_resumen_mensual(wb, months, txn_last_row)
        gasto_meta = build_gasto_por_categoria(wb, months, categories, txn_last_row)
        build_meta_de_ahorro(wb, goal, projection)
        build_dashboard(wb, resumen_meta, gasto_meta, patrimonio_meta, categories, projection)

    # Dashboard is built last (it references the other sheets by name, so
    # they have to exist first) but belongs first — it's the landing view.
    wb.move_sheet("Dashboard", offset=-(len(wb.sheetnames) - 1))
    wb.active = 0
    # Excel/Sheets recalculate everything on open regardless of the cached
    # values written below, so the two can never disagree in a real app.
    wb.calculation.fullCalcOnLoad = True

    wb.save(out_path)

    # Not optional: without cached values every chart points at cells that
    # read as blank, so the charts render as empty frames outside a real
    # spreadsheet app. See scripts/cache_values.py for the full reasoning.
    stats = inject_cached_values(out_path)
    print(
        f"Escrito: {out_path} "
        f"({stats['written']}/{stats['formulas']} fórmulas con valor precalculado)"
    )
    if stats["errors"]:
        print(f"ADVERTENCIA: {len(stats['errors'])} celda(s) con error de fórmula:")
        for location in stats["errors"][:20]:
            print(f"  {location}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
