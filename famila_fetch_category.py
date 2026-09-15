import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

API = "https://api.cosicomodo.it/occ/v2"
WEB = "https://www.cosicomodo.it"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
BOOTSTRAP_PATH = "/familasud/teverola/reparti/prodotti-alimentari/c/10012"
PAGE_SIZE = 20


def bootstrap_session(session, expected_site, expected_store, retries=7):
    page_url = WEB + BOOTSTRAP_PATH
    last = None
    for attempt in range(retries):
        r = session.get(
            page_url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
            },
            timeout=60,
        )
        last = r
        if r.status_code == 200:
            break
        if r.status_code in (429, 481, 482, 500, 502, 503, 504):
            time.sleep(min(75, 8 * (attempt + 1)))
            continue
        r.raise_for_status()
    else:
        raise RuntimeError(
            f"Famila bootstrap fallito: HTTP {last.status_code if last is not None else 'ERR'}"
        )

    soup = BeautifulSoup(r.text, "html.parser")
    node = soup.find("script", id="__NEXT_DATA__")
    if not node or not node.string:
        raise RuntimeError("Famila bootstrap: __NEXT_DATA__ mancante")

    pp = ((json.loads(node.string).get("props") or {}).get("pageProps") or {})
    site = str(pp.get("originSiteWithSubBrand") or pp.get("originSite") or "").strip()
    store = str(pp.get("storeAliasId") or "").strip()
    point = pp.get("pointOfService") or {}
    display = str(point.get("displayName") or point.get("description") or "").strip()

    if site != expected_site:
        raise RuntimeError(f"Famila bootstrap site inatteso: {site!r} != {expected_site!r}")
    if store != expected_store:
        raise RuntimeError(f"Famila bootstrap store inatteso: {store!r} != {expected_store!r}")
    if not display or "famila" not in display.lower():
        raise RuntimeError(f"Famila bootstrap punto vendita non validato: {display!r}")

    return page_url, display


def request_json(session, url, params, referer, retries=7):
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
        r = session.get(url, params=params, headers=headers, timeout=60)
        last = r
        if r.status_code == 200:
            data = r.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"Risposta JSON Famila inattesa: {type(data).__name__}")
            return data, r.url
        if r.status_code in (429, 481, 482, 500, 502, 503, 504):
            time.sleep(min(60, 6 * (attempt + 1)))
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.url}")
    raise RuntimeError(f"HTTP {last.status_code if last else 'ERR'} after retries")


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

    ref = p.get("referencePrice") or p.get("unitPrice") or {}
    if isinstance(ref, dict) and ref:
        ref_value = fnum(ref.get("value"))
        ref_formatted = str(ref.get("formattedValue") or "")
        ref_unit = ref.get("unit") or ref.get("unitType") or ref.get("unitOfMeasure") or ""
        if isinstance(ref_unit, dict):
            ref_unit = ref_unit.get("code") or ref_unit.get("name") or ""
    else:
        ref_value = fnum(price.get("priceReferenceUnit")) if isinstance(price, dict) else None
        ref_formatted = ""
        ref_unit = str(price.get("referenceUnitMeasure") or "") if isinstance(price, dict) else ""

    stock = p.get("stock") or {}
    availability = ""
    if isinstance(stock, dict):
        availability = str(stock.get("stockLevelStatus") or "")

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
    ap.add_argument("--site", default="familasud")
    ap.add_argument("--store", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).isoformat()
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.7"})
    referer, store_display = bootstrap_session(session, args.site, args.store)

    url = f"{API}/{args.site}/stores/{args.store}/users/anonymous/products/search-by-category"

    first, first_url = request_json(
        session,
        url,
        {
            "categoryCode": args.code,
            "currentPage": 0,
            "pageSize": PAGE_SIZE,
            "fields": "FULL",
        },
        referer,
    )
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
            time.sleep(0.18)
            data, source_url = request_json(
                session,
                url,
                {
                    "categoryCode": args.code,
                    "currentPage": page,
                    "pageSize": PAGE_SIZE,
                    "fields": "FULL",
                },
                referer,
            )
        current = (data.get("pagination") or {}).get("currentPage")
        if current is None or int(current) != page:
            raise SystemExit(f"Pagina errata {args.code}: attesa={page}, ricevuta={current}")
        raw_products = data.get("products") or []
        if not isinstance(raw_products, list):
            raise SystemExit(f"Categoria {args.code}: campo products non valido a pagina {page}")
        received += len(raw_products)
        for p in raw_products:
            if not isinstance(p, dict):
                continue
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
        "store_display": store_display,
        "total_results": total,
        "received": received,
        "positive_price_products": len(dedup),
        "products": list(dedup.values()),
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(
        f"FAMILA_CATEGORY_OK code={args.code} total={total} "
        f"positive={len(dedup)} pages={pages} store={store_display}"
    )


if __name__ == "__main__":
    main()
