import json
import sqlite3
from pathlib import Path

# V81 QA export trigger 2026-09-19\n# artifact-enabled trigger\nDB = Path("prezzi_conad_capodrise_app.db")
OUT = Path("conad_v81_android_products.jsonl")

def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT
          product_code, supermarket, store_name, product_name, brand,
          category1, quantity_value, quantity_unit, price_eur,
          unit_price, unit_price_unit, variable_weight, source_queries, checked_at
        FROM products_current
        WHERE price_eur > 0
        ORDER BY product_code
    """).fetchall()
    con.close()

    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            obj = {
                "key": r["product_code"],
                "supermarket": r["supermarket"] or "Conad",
                "store": r["store_name"],
                "name": r["product_name"],
                "brand": r["brand"],
                "category": r["category1"],
                "quantityValue": r["quantity_value"],
                "quantityUnit": r["quantity_unit"],
                "priceEur": r["price_eur"],
                "unitPriceEur": r["unit_price"],
                "unitPriceUnit": r["unit_price_unit"],
                "variableWeight": bool(r["variable_weight"]),
                "sourceUrl": r["source_queries"],
                "checkedAt": r["checked_at"],
            }
            f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")

    print(json.dumps({"rows": len(rows), "output": str(OUT)}, ensure_ascii=False))
    if len(rows) != 733:
        raise RuntimeError(f"Attesi 733 prodotti Android V81, trovati {len(rows)}")

if __name__ == "__main__":
    main()
