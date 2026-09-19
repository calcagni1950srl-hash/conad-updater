import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import CONAD_CAPODRISE_ENGINE as engine

AUDIT_OUT = Path("conad_v81_overlay_audit.json")


def read_meta(con, key):
    row = con.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def main():
    if not engine.FULL_DB.exists():
        raise RuntimeError(f"DB base mancante: {engine.FULL_DB}")

    con = sqlite3.connect(engine.FULL_DB)
    stores = [r[0] for r in con.execute(
        "SELECT DISTINCT store_code FROM products_current ORDER BY store_code"
    ).fetchall()]
    rows_before = con.execute("SELECT COUNT(*) FROM products_current").fetchone()[0]
    positive_before = con.execute(
        "SELECT COUNT(*) FROM products_current WHERE price_eur > 0"
    ).fetchone()[0]
    local_before = con.execute(
        "SELECT COUNT(*) FROM products_current WHERE product_code LIKE 'FLYER:%'"
    ).fetchone()[0]
    bassi_before = con.execute(
        "SELECT COUNT(*) FROM products_current WHERE bassi_fissi=1"
    ).fetchone()[0]
    flyer_valid_to = read_meta(con, "local_flyer_valid_to")
    con.close()

    if stores != [engine.STORE_CODE]:
        raise RuntimeError(f"Store inattesi nel DB base: {stores}")
    if rows_before != positive_before:
        raise RuntimeError("DB base contiene prezzi non positivi")
    if bassi_before < 700 or local_before < 40:
        raise RuntimeError(
            f"DB base troppo povero: bassi={bassi_before}, local={local_before}"
        )
    if flyer_valid_to:
        valid_to = date.fromisoformat(flyer_valid_to)
        if date.today() > valid_to:
            raise RuntimeError(
                f"Volantino locale base scaduto il {flyer_valid_to}; richiede refresh completo"
            )

    visual = engine.apply_visual_verified_offers()
    refs = engine.apply_fixed_reference_prices()
    engine.build_app_db()

    full = engine.database_stats(engine.FULL_DB)
    app = engine.database_stats(engine.APP_DB)

    con = sqlite3.connect(engine.FULL_DB)
    ref_rows = con.execute(
        """
        SELECT product_code, product_name, quantity_value, quantity_unit,
               price_eur, unit_price, unit_price_unit, variable_weight
        FROM products_current
        WHERE product_code LIKE 'REF:%'
        ORDER BY product_code
        """
    ).fetchall()
    visual_rows = con.execute(
        """
        SELECT product_code, product_name, quantity_value, quantity_unit,
               price_eur, unit_price, unit_price_unit
        FROM products_current
        WHERE product_code LIKE 'VISUAL:%'
        ORDER BY product_code
        """
    ).fetchall()
    con.close()

    if len(ref_rows) != 5:
        raise RuntimeError(f"Attesi 5 riferimenti medi, trovati {len(ref_rows)}")
    if len(visual_rows) != 1:
        raise RuntimeError(f"Attesa 1 offerta visuale attiva, trovate {len(visual_rows)}")

    expected = {
        "REF:AVG_AGLIO": (0.050, 0.40, 8.0),
        "REF:AVG_CIPOLLA": (0.250, 0.45, 1.8),
        "REF:AVG_PREZZEMOLO": (0.030, 0.36, 12.0),
        "REF:AVG_ROSMARINO": (0.030, 1.47, 49.0),
        "REF:AVG_PEPERONCINO": (0.020, 0.67, 33.5),
    }
    for code, _name, qty, _unit, price, unit_price, _unit_price_unit, variable in ref_rows:
        if code not in expected:
            raise RuntimeError(f"Riferimento non autorizzato: {code}")
        eqty, eprice, eunit = expected[code]
        if abs(float(qty)-eqty) > 1e-9:
            raise RuntimeError(f"Quantita errata {code}: {qty}")
        if abs(float(price)-eprice) > 1e-9:
            raise RuntimeError(f"Prezzo errato {code}: {price}")
        if abs(float(unit_price)-eunit) > 0.01:
            raise RuntimeError(f"Prezzo unitario errato {code}: {unit_price}")
        if int(variable) != 1:
            raise RuntimeError(f"variable_weight non attivo per {code}")

    payload = {
        "status": "OK",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "store_code": engine.STORE_CODE,
        "base": {
            "rows": rows_before,
            "positive": positive_before,
            "bassi_fissi": bassi_before,
            "local_offers": local_before,
            "local_flyer_valid_to": flyer_valid_to,
        },
        "visual_verified_offers": visual,
        "fixed_reference_prices": refs,
        "reference_rows": [
            {
                "product_code": r[0],
                "product_name": r[1],
                "quantity_value": r[2],
                "quantity_unit": r[3],
                "price_eur": r[4],
                "unit_price": r[5],
                "unit_price_unit": r[6],
                "variable_weight": r[7],
            }
            for r in ref_rows
        ],
        "visual_rows": [
            {
                "product_code": r[0],
                "product_name": r[1],
                "quantity_value": r[2],
                "quantity_unit": r[3],
                "price_eur": r[4],
                "unit_price": r[5],
                "unit_price_unit": r[6],
            }
            for r in visual_rows
        ],
        "full_db": full,
        "app_db": app,
    }
    AUDIT_OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
