import json
import sys
from pathlib import Path
import requests

API = "https://api.cosicomodo.it/occ/v2"
SITE = "sole365"
STORE = "marcianise-aurno"
CATEGORY = "10006"
PAGES = [0, 1, 2]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    "Origin": "https://www.cosicomodo.it",
    "Referer": "https://www.cosicomodo.it/",
}

def safe_float(v):
    try:
        return float(v)
    except Exception:
        return None

def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    endpoint = f"{API}/{SITE}/stores/{STORE}/users/anonymous/products/search-by-category"
    audit = {
        "probe": "SOLE365_OCC_PROBE",
        "baseSiteId": SITE,
        "storeAliasId": STORE,
        "categoryCode": CATEGORY,
        "endpoint": endpoint,
        "pages": {},
        "errors": [],
    }

    all_codes = []
    positive_prices = 0
    total_products = 0

    for page in PAGES:
        params = {
            "categoryCode": CATEGORY,
            "currentPage": page,
            "pageSize": 20,
            "fields": "FULL",
        }
        try:
            r = session.get(endpoint, params=params, timeout=60)
            info = {"http_status": r.status_code, "request_url": r.url}

            if r.status_code != 200:
                info["body_preview"] = r.text[:1000]
                audit["pages"][str(page)] = info
                audit["errors"].append(f"page {page}: HTTP {r.status_code}")
                continue

            data = r.json()
            pagination = data.get("pagination") or {}
            products = data.get("products") or []

            codes = []
            sample = []
            page_positive = 0

            for p in products:
                code = str(p.get("code") or "").strip()
                name = str(p.get("name") or "").strip()
                price_obj = p.get("price") or {}
                price = safe_float(price_obj.get("value")) if isinstance(price_obj, dict) else None

                if code:
                    codes.append(code)
                    all_codes.append(code)
                if price is not None and price > 0:
                    positive_prices += 1
                    page_positive += 1

                if len(sample) < 5:
                    stock = p.get("stock") or {}
                    sample.append({
                        "code": code,
                        "name": name,
                        "price": price,
                        "price_formatted": price_obj.get("formattedValue") if isinstance(price_obj, dict) else None,
                        "stock": stock.get("stockLevelStatus") if isinstance(stock, dict) else None,
                    })

            total_products += len(products)
            info.update({
                "currentPage": pagination.get("currentPage"),
                "pageSize": pagination.get("pageSize"),
                "totalPages": pagination.get("totalPages"),
                "totalResults": pagination.get("totalResults"),
                "products_count": len(products),
                "positive_price_count": page_positive,
                "codes": codes,
                "sample_products": sample,
            })
            audit["pages"][str(page)] = info

            if pagination.get("currentPage") != page:
                audit["errors"].append(
                    f"page {page}: currentPage mismatch ({pagination.get('currentPage')})"
                )
            if not products:
                audit["errors"].append(f"page {page}: no products")

        except Exception as exc:
            audit["errors"].append(f"page {page}: {repr(exc)}")

    sets = [set((audit["pages"].get(str(p)) or {}).get("codes") or []) for p in PAGES]
    distinct_pages = True
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            if sets[i] and sets[i] == sets[j]:
                distinct_pages = False

    audit["summary"] = {
        "pages_requested": len(PAGES),
        "total_products_received": total_products,
        "unique_product_codes": len(set(all_codes)),
        "positive_prices": positive_prices,
        "pages_are_distinct": distinct_pages,
    }

    success = (
        not audit["errors"]
        and total_products > 0
        and positive_prices > 0
        and distinct_pages
        and all((audit["pages"].get(str(p)) or {}).get("http_status") == 200 for p in PAGES)
    )

    audit["verdict"] = (
        "SOLE365_OCC_API_PAGINATION_VALIDATED"
        if success
        else "SOLE365_OCC_API_NOT_VALIDATED"
    )

    Path("sole365_probe_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
