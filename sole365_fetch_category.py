import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://api.cosicomodo.it/occ/v2"
WEB = "https://www.cosicomodo.it"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
PAGE_SIZE = 20
VALID_SITE = "sole365"
VALID_STORE = "marcianise-aurno"
VALID_DISPLAY = "Sole365 - Marcianise"


def request_json(session, url, params, referer, retries=8):
    last = None
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        "Origin": WEB,
        "Referer": referer,
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, headers=headers, timeout=70)
        except requests.RequestException as exc:
            last = exc
            time.sleep(min(60, 5 * (attempt + 1)))
            continue
        last = r
        if r.status_code == 200:
            data = r.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"Risposta JSON Sole365 inattesa: {type(data).__name__}")
            return data, r.url
        if r.status_code in (429, 481, 482, 500, 502, 503, 504):
            time.sleep(min(90, 7 * (attempt + 1)))
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.url}")
    if hasattr(last, "status_code"):
        raise RuntimeError(f"HTTP {last.status_code} after retries")
    raise RuntimeError(f"Errore rete dopo retries: {last!r}")


def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


def normalize_product(p, category_code, category_name, source_url, site, store, stamp):
    price = p.get("price") or p.get("bestPrice") or {}
    price_eur = fnum(price.get("value")) if isinstance(price, dict) else None
    if not price_eur or price_eur <= 0:
        return None

    code = str(p.get("code") or p.get("ean") or "").strip()
    name = str(p.get("name") or p.get("description") or "").strip()
    if not code or not name:
        return None

    brand = p.get("brand") or p.get("marca") or ""
    if isinstance(brand, dict):
        brand = brand.get("name") or brand.get("code") or ""

    # CosìComodo espone due formati. Nei freschi Sole365 il riferimento €/kg
    # è dentro price.priceReferenceUnit + price.referenceUnitMeasure; per altri
    # articoli può comparire come referencePrice/unitPrice.
    ref = p.get("referencePrice") or p.get("unitPrice") or {}
    if isinstance(ref, dict) and ref:
        ref_value = fnum(ref.get("value"))
        ref_formatted = str(ref.get("formattedValue") or "")
        ref_unit = (
            ref.get("unit") or ref.get("unitType") or ref.get("unitOfMeasure")
            or ref.get("referenceUnitMeasure") or ""
        )
        if isinstance(ref_unit, dict):
            ref_unit = ref_unit.get("code") or ref_unit.get("name") or ""
    else:
        ref_value = fnum(price.get("priceReferenceUnit")) if isinstance(price, dict) else None
        ref_formatted = ""
        ref_unit = str(price.get("referenceUnitMeasure") or "") if isinstance(price, dict) else ""

    stock = p.get("stock") or {}
    availability = str(stock.get("stockLevelStatus") or "") if isinstance(stock, dict) else ""

    return {
        "product_id": code,
        "product_name": name,
        "brand": str(brand or ""),
        "category_code": category_code,
        "category_name": category_name,
        "price_eur": price_eur,
        "price_formatted": str(price.get("formattedValue") or "") if isinstance(price, dict) else "",
        "unit_price": ref_value,
        "unit_price_formatted": ref_formatted,
        "unit_price_unit": str(ref_unit or ""),
        "availability_raw": availability,
        "product_url": str(p.get("url") or p.get("productUrl") or ""),
        "source_url": source_url,
        "base_site_id": site,
        "store_alias_id": store,
        "detected_at": stamp,
        "raw_json": p,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=VALID_SITE)
    ap.add_argument("--store", default=VALID_STORE)
    ap.add_argument("--code", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    if args.site != VALID_SITE or args.store != VALID_STORE:
        raise SystemExit(f"Punto vendita Sole365 non validato: {args.site}/{args.store}")

    stamp = datetime.now(timezone.utc).isoformat()
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.7"})
    referer = f"{WEB}/{args.site}/{args.store}/reparti/prodotti-alimentari/c/{args.code}"
    url = f"{API}/{args.site}/stores/{args.store}/users/anonymous/products/search-by-category"

    first, first_url = request_json(session, url, {
        "categoryCode": args.code,
        "currentPage": 0,
        "pageSize": PAGE_SIZE,
        "fields": "FULL",
    }, referer)
    pagination = first.get("pagination") or {}
    total = int(pagination.get("totalResults") or 0)
    pages = int(pagination.get("totalPages") or 0)
    current0 = pagination.get("currentPage")
    if current0 is None or int(current0) != 0:
        raise SystemExit(f"Categoria {args.code}: prima pagina errata, currentPage={current0}")
    if total <= 0 or pages <= 0:
        raise SystemExit(f"Categoria {args.code} vuota/non valida: total={total}, pages={pages}")

    products = []
    received = 0
    for page in range(pages):
        if page == 0:
            data, source_url = first, first_url
        else:
            time.sleep(0.30)
            data, source_url = request_json(session, url, {
                "categoryCode": args.code,
                "currentPage": page,
                "pageSize": PAGE_SIZE,
                "fields": "FULL",
            }, referer)
        current = (data.get("pagination") or {}).get("currentPage")
        if current is None or int(current) != page:
            raise SystemExit(f"Pagina errata {args.code}: attesa={page}, ricevuta={current}")
        raw_products = data.get("products") or []
        if not isinstance(raw_products, list):
            raise SystemExit(f"Categoria {args.code}: campo products non valido a pagina {page}")
        received += len(raw_products)
        for p in raw_products:
            if isinstance(p, dict):
                row = normalize_product(p, args.code, args.name, source_url, args.site, args.store, stamp)
                if row:
                    products.append(row)

    if received != total:
        raise SystemExit(f"Categoria {args.code} incompleta: dichiarati={total}, ricevuti={received}")

    dedup = {p["product_id"]: p for p in products}
    if not dedup:
        raise SystemExit(f"Categoria {args.code}: nessun prodotto con prezzo positivo")

    result = {
        "category_code": args.code,
        "category_name": args.name,
        "store_display": VALID_DISPLAY,
        "total_results": total,
        "received": received,
        "positive_price_products": len(dedup),
        "products": list(dedup.values()),
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"SOLE365_CATEGORY_OK code={args.code} total={total} positive={len(dedup)} pages={pages} store={VALID_DISPLAY}")


if __name__ == "__main__":
    main()
