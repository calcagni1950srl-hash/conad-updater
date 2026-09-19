import json
import sqlite3
import urllib.request
from pathlib import Path

TERMS = ["rosmarino", "peperoncino"]
LOCAL_DBS = {
    "Decò": Path("prezzi_deco.db"),
    "Famila": Path("prezzi_famila_app.db"),
    "Sole365": Path("prezzi_sole365_app.db"),
}
PICCOLO_URL = "https://raw.githubusercontent.com/calcagni1950srl-hash/piccolo-updater/main/prezzi.db"
PICCOLO_DB = Path("probe_prezzi_piccolo.db")


def qident(name):
    return '"' + name.replace('"', '""') + '"'


def scan_db(label, path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    out = {"db": str(path), "tables": {}, "hits": {t: [] for t in TERMS}}
    tables = [
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({qident(table)})")]
        text_cols = []
        for r in con.execute(f"PRAGMA table_info({qident(table)})"):
            ctype = (r[2] or "").upper()
            if "CHAR" in ctype or "TEXT" in ctype or ctype == "":
                text_cols.append(r[1])
        out["tables"][table] = cols
        if not text_cols:
            continue
        for term in TERMS:
            where = " OR ".join(f"LOWER(CAST({qident(c)} AS TEXT)) LIKE ?" for c in text_cols)
            params = ["%" + term + "%"] * len(text_cols)
            try:
                rows = con.execute(
                    f"SELECT * FROM {qident(table)} WHERE {where} LIMIT 80", params
                ).fetchall()
            except Exception:
                continue
            for row in rows:
                item = {k: row[k] for k in row.keys()}
                item["_table"] = table
                out["hits"][term].append(item)
    con.close()
    return out


def main():
    with urllib.request.urlopen(PICCOLO_URL, timeout=120) as r:
        PICCOLO_DB.write_bytes(r.read())

    results = {}
    for label, path in {**LOCAL_DBS, "Piccolo": PICCOLO_DB}.items():
        if not path.exists():
            results[label] = {"error": f"missing {path}"}
            continue
        try:
            results[label] = scan_db(label, path)
        except Exception as exc:
            results[label] = {"error": repr(exc)}

    Path("v81_staple_reference_probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({
        label: {
            term: len((data.get("hits") or {}).get(term, []))
            for term in TERMS
        } if "error" not in data else {"error": data["error"]}
        for label, data in results.items()
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
