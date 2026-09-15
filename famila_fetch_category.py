import argparse, json, math, time
from datetime import datetime, timezone
from pathlib import Path
import requests

API = "https://api.cosicomodo.it/occ/v2"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"


def request_json(session, url, params, retries=7):
    last = None
    for attempt in range(retries):
        r = session.get(
            url,
            params=params,
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=60,
        )
        last = r
        if r.status_code == 200:
            return r.json(), r.url
        if r.status_code in (429, 481, 500, 502, 503, 504):
            time.sleep(min(75, 8 * (attempt + 1)))
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.url}")
    raise RuntimeError(f"HTTP {last.status_code if last else 'ERR'} after retries")


def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


def normalize_product(p, category_code, category_name, source_url, site, store, stamp):
    price = p.get("price") or {}
    price_eur = fnum(price.get("value")) if isinstance(price, dict) else None
    if not price_eur or price_eur <= 0:
        return None

    code = str(p.get("code") or p.get("ean") or "").strip()
    name = str(p.get("name") or "").strip()
    if not code or not name:
        return None

    brand = p.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name") or brand.get("code")

    ref = p.get("referencePrice") or p.get("unitPrice") or {}
    ref_value = fnum(ref.get("value")) if isinstance(ref, dict) else None
    ref_unit = ""
    if isinstance(ref, dict):
        ref_unit = ref.get("unit") or ref.get("unitType") or ref.get("unitOfMeasure") or ""
        if isinstance(ref_unit, dict):
            ref_unit = ref_unit.get("code") or ref_unit.get("name") or ""

    stock = p.get("stock") or {}
    return {
        "product_id": code,
        "product_name": name,
        "brand": str(brand or ""),
        "category_code": category_code,
        "category_name": category_name,
        "price_eur": price_eur,
        "price_formatted": str(price.get("formattedValue") or ""),
        "unit_price": ref_value,
        "unit_price_formatted": str(ref.get("formattedValue") or "") if isinstance(ref, dict) else "",
        "unit_price_unit": str(ref_unit or ""),
        "availability_raw": str(stock.get("stockLevelStatus") or "") if isinstance(stock, dict) else "",
        "product_url": str(p.get("url") or p.get("productUrl") or ""),
        "source_url": source_url,
        "base_site_id": site,
        "store_alias_id": store,
        "detected_at": stamp,
        "raw_json": p,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="familasud")
    ap.add_argument("--store", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).isoformat()
    session = requests.Session()
    url = f"{API}/{args.site}/stores/{args.store}/users/anonymous/products/search-by-category"

    first, first_url = request_json(session, url, {
        "categoryCode": args.code,
        "currentPage": 0,
        "pageSize": 100,
        "fields": "FULL",
    })
    pagination = first.get("pagination") or {}
    total = int(pagination.get("totalResults") or 0)
    pages = int(pagination.get("totalPages") or 0)
    if total <= 0 or pages <= 0:
        raise SystemExit(f"Categoria {args.code} vuota/non valida: total={total}, pages={pages}")

    products = []
    received = 0
    for page in range(pages):
        if page == 0:
            data, source_url = first, first_url
        else:
            time.sleep(0.35)
            data, source_url = request_json(session, url, {
                "categoryCode": args.code,
                "currentPage": page,
                "pageSize": 100,
                "fields": "FULL",
            })
        current = (data.get("pagination") or {}).get("currentPage")
        if current is None or int(current) != page:
            raise SystemExit(f"Pagina errata {args.code}: attesa={page}, ricevuta={current}")
        raw_products = data.get("products") or []
        received += len(raw_products)
        for p in raw_products:
            row = normalize_product(p, args.code, args.name, source_url, args.site, args.store, stamp)
            if row:
                products.append(row)

    if received != total:
        raise SystemExit(f"Categoria {args.code} incompleta: dichiarati={total}, ricevuti={received}")

    dedup = {p["product_id"]: p for p in products}
    result = {
        "category_code": args.code,
        "category_name": args.name,
        "total_results": total,
        "received": received,
        "positive_price_products": len(dedup),
        "products": list(dedup.values()),
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"FAMILA_CATEGORY_OK code={args.code} total={total} positive={len(dedup)}")


if __name__ == "__main__":
    main()
