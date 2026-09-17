import json
import sqlite3
from pathlib import Path

DB = "prezzi_conad.db"
STORE = "CONAD-GENERICO"
REQUIRED = ("latte", "pasta", "uova")

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

before = cur.execute(
    "SELECT COUNT(*) FROM products_current WHERE store_code=?", (STORE,)
).fetchone()[0]
zero_or_negative = cur.execute(
    "SELECT COUNT(*) FROM products_current WHERE store_code=? AND price_eur<=0", (STORE,)
).fetchone()[0]

cur.execute(
    "DELETE FROM products_current WHERE store_code=? AND price_eur<=0", (STORE,)
)
con.commit()

after = cur.execute(
    "SELECT COUNT(*) FROM products_current WHERE store_code=?", (STORE,)
).fetchone()[0]
invalid_after = cur.execute(
    "SELECT COUNT(*) FROM products_current WHERE store_code=? AND price_eur<=0", (STORE,)
).fetchone()[0]

checks = {}
for term in REQUIRED:
    count = cur.execute(
        "SELECT COUNT(*) FROM products_current WHERE store_code=? AND price_eur>0 AND lower(product_name) LIKE ?",
        (STORE, f"%{term}%"),
    ).fetchone()[0]
    checks[term] = count

qty_complete = cur.execute(
    """SELECT COUNT(*) FROM products_current
       WHERE store_code=? AND price_eur>0
       AND quantity_value IS NOT NULL AND quantity_value>0
       AND quantity_unit IS NOT NULL AND trim(quantity_unit)<>''""",
    (STORE,),
).fetchone()[0]

report = {
    "db": DB,
    "store": STORE,
    "rows_before_filter": before,
    "removed_zero_or_negative": zero_or_negative,
    "rows_positive_after_filter": after,
    "invalid_after_filter": invalid_after,
    "quantity_value_unit_complete": qty_complete,
    "required_positive_products": checks,
}

Path("conad_positive_filter_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False, indent=2))

con.close()

if after <= 0:
    raise SystemExit("FAIL: no positive Conad products")
if invalid_after != 0:
    raise SystemExit("FAIL: non-positive prices remain")
missing = [name for name, count in checks.items() if count <= 0]
if missing:
    raise SystemExit("FAIL: required products missing: " + ", ".join(missing))
