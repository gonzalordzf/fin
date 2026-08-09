"""Manual smoke test against a real downloaded statement (not committed to
the repo — path is passed via env var so this stays runnable locally
without shipping personal financial data)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.bbva import parse_bbva_statement  # noqa: E402

PDF_PATH = os.environ["BBVA_TEST_PDF"]
PASSWORD = os.environ["BBVA_TEST_PASSWORD"]

txns = parse_bbva_statement(PDF_PATH, PASSWORD)

print(f"Parsed {len(txns)} transactions\n")
cargos = [t for t in txns if t.amount < 0]
abonos = [t for t in txns if t.amount > 0]
print(f"Cargos: {len(cargos)}  total = {sum(t.amount for t in cargos):.2f}")
print(f"Abonos: {len(abonos)}  total = {sum(t.amount for t in abonos):.2f}\n")

for t in txns:
    print(
        f"{t.operation_date} / {t.liquidation_date}  {t.amount:>12.2f}  "
        f"ref={t.external_ref}  bal_op={t.balance_operacion}  bal_liq={t.balance_liquidacion}  "
        f"{t.description}"
    )
