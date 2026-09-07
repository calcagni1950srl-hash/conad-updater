#!/usr/bin/env python3
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlencode, urlsplit, urlunsplit, parse_qsl

import requests
from bs4 import BeautifulSoup

BASE = "https://www.cosicomodo.it"
SEEDS = [
    "https://www.cosicomodo.it/familasud/teverola/ricerca",
    "https://www.cosicomodo.it/familasud/foggia-addedda/reparti/tutto-per-la-casa/c/10016",
]
OUT = Path("famila_v5_artifact")
OUT.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url, label):
    rec = {"url": url, "status": None, "final_url": None, "bytes": 0, "elapsed_s": None, "error": None}
    t0 = time.time()
    try:
        r = session.get(url, timeout=45, allow_redirects=True)
        rec.update({
            "status": r.status_code,
            "final_url": r.url,
            "bytes": len(r.content),
            "elapsed_s": round(time.time() - t0, 3),
            "content_type": r.headers.get("content-type", ""),
        })
        Path(OUT / f"{label}.html").write_bytes(r.content)
        return rec, r.text
    except Exception as e:
        rec["elapsed_s"] = round(time.time() - t0, 3)
        rec["error"] = repr(e)
        return rec, ""


def parse_next_data(html):
    if not html:
        return None, "empty_html"
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("script", id="__NEXT_DATA__")
    if not node or not node.string:
        return None, "next_data_missing"
    try:
        return json.loads(node.string), None
    except Exception as e:
        return None, f"next_data_json_error:{e!r}"


def product_snapshot(p):
    price = p.get("price") or {}
    best = p.get("bestPrice") or {}
    stock = p.get("stock") or {}
    return {
        "code": p.get("code"),
        "name": p.get("name"),
        "url": p.get("url"),
        "leafCategoryName": p.get("leafCategoryName"),
        "price_value": price.get("value"),
        "price_formatted": price.get("formattedValue"),
        "price_reference_unit": price.get("priceReferenceUnit"),
        "reference_unit_measure": price.get("referenceUnitMeasure"),
        "best_price_value": best.get("value"),
        "best_price_reference_unit": best.get("priceReferenceUnit"),
        "best_reference_unit_measure": best.get("referenceUnitMeasure"),
        "stock_status": stock.get("stockLevelStatus"),
    }


def extract_page(html):
    data, err = parse_next_data(html)
    if err:
        return {"valid_next_data": False, "error": err}
    pp = data.get("props", {}).get("pageProps", {})
    sp = pp.get("searchPageData") or {}
    products = sp.get("products") or []
    pagination = sp.get("pagination") or {}
    cats = pp.get("firstLevelCategories") or []
    pos = pp.get("pointOfService") or {}
    address = pos.get("address") or {}

    category_rows = []
    for c in cats:
        code = c.get("categorycode")
        alt = c.get("alternativeCategoryUrl")
        if code and alt:
            category_rows.append({
                "code": str(code),
                "name": c.get("name") or c.get("categoryName") or c.get("label"),
                "alternativeCategoryUrl": alt,
            })

    valid_prices = 0
    valid_unit_refs = 0
    for p in products:
        price = p.get("price") or {}
        try:
            if float(price.get("value")) > 0:
                valid_prices += 1
        except Exception:
            pass
        if price.get("referenceUnitMeasure") and price.get("priceReferenceUnit") is not None:
            valid_unit_refs += 1

    return {
        "valid_next_data": True,
        "build_id": data.get("buildId"),
        "page_path": pp.get("pagePath"),
        "origin_site": pp.get("originSite"),
        "store_alias_id": pp.get("storeAliasId"),
        "service_type": pp.get("serviceType"),
        "store": {
            "displayName": pos.get("displayName"),
            "description": pos.get("description"),
            "selexCode": pos.get("selexCode"),
            "site": pos.get("site"),
            "socio": pos.get("socio"),
            "store": pos.get("store"),
            "address": address.get("formattedAddress"),
            "postalCode": address.get("postalCode"),
            "town": address.get("town"),
        },
        "search_page_present": bool(sp),
        "category_code": sp.get("categoryCode"),
        "pagination": pagination,
        "product_count": len(products),
        "positive_price_count": valid_prices,
        "unit_reference_count": valid_unit_refs,
        "product_codes": [str(p.get("code")) for p in products if p.get("code")],
        "products_sample": [product_snapshot(p) for p in products[:5]],
        "first_level_category_count": len(category_rows),
        "first_level_categories": category_rows,
    }


def with_query(url, **kwargs):
    s = urlsplit(url)
    q = dict(parse_qsl(s.query, keep_blank_values=True))
    for k, v in kwargs.items():
        q[k] = str(v)
    return urlunsplit((s.scheme, s.netloc, s.path, urlencode(q), s.fragment))


def category_url_from_seed(seed_url, alt):
    # Preserve /familasud/<store>/ prefix from the seed.
    m = re.match(r"^(https://www\.cosicomodo\.it/familasud/[^/]+)", seed_url)
    if not m:
        return urljoin(BASE, alt)
    return m.group(1) + (alt if alt.startswith("/") else "/" + alt)


def page_signature(parsed):
    if not parsed.get("valid_next_data"):
        return None
    return {
        "currentPage": (parsed.get("pagination") or {}).get("currentPage"),
        "codes": parsed.get("product_codes") or [],
    }


def test_pagination(base_url, parsed_base, prefix):
    results = []
    base_sig = page_signature(parsed_base)
    if not base_sig or not parsed_base.get("search_page_present"):
        return results

    # Common SAP Commerce / storefront query variants. We do not assume which one works.
    candidates = [
        with_query(base_url, page=1),
        with_query(base_url, page=2),
        with_query(base_url, q=":relevance", page=1),
        with_query(base_url, q=":relevance", page=2),
    ]
    seen = set()
    for idx, url in enumerate(candidates, 1):
        if url in seen:
            continue
        seen.add(url)
        f, html = fetch(url, f"{prefix}_pagination_{idx}")
        parsed = extract_page(html)
        sig = page_signature(parsed)
        changed_codes = bool(sig and base_sig and sig["codes"] and sig["codes"] != base_sig["codes"])
        current_page_changed = bool(sig and sig.get("currentPage") != base_sig.get("currentPage"))
        results.append({
            "fetch": f,
            "parsed": parsed,
            "changed_codes": changed_codes,
            "current_page_changed": current_page_changed,
            "pagination_validated": bool(f.get("status") == 200 and parsed.get("valid_next_data") and changed_codes and current_page_changed),
        })
        time.sleep(2)
    return results


def main():
    report = {
        "probe": "Famila NextData Probe V5",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "browser_automation": False,
        "method": "HTTP direct + __NEXT_DATA__ JSON",
        "notes": [
            "Non costruisce ancora il database definitivo.",
            "Non inventa negozi, prezzi, quantità o disponibilità.",
            "Valida i dati strutturati Next.js realmente presenti nelle risposte HTTP.",
            "La validazione paginazione richiede sia currentPage diverso sia codici prodotto diversi dalla pagina base.",
        ],
        "seeds": [],
    }

    pagination_any = False
    category_any = False
    products_any = False
    prices_any = False
    unit_refs_any = False

    for si, seed in enumerate(SEEDS, 1):
        f, html = fetch(seed, f"seed_{si}")
        parsed = extract_page(html)
        seed_rec = {"fetch": f, "parsed": parsed, "category_tests": [], "pagination_tests": []}

        if f.get("status") == 200 and parsed.get("valid_next_data"):
            products_any |= parsed.get("product_count", 0) > 0
            prices_any |= parsed.get("positive_price_count", 0) > 0
            unit_refs_any |= parsed.get("unit_reference_count", 0) > 0

            # If seed itself is a category page, test its pagination.
            if parsed.get("search_page_present") and parsed.get("product_count", 0) > 0:
                seed_rec["pagination_tests"] = test_pagination(seed, parsed, f"seed_{si}")
                if any(x.get("pagination_validated") for x in seed_rec["pagination_tests"]):
                    pagination_any = True

            # Test a small sample of category URLs discovered from structured data.
            cats = parsed.get("first_level_categories") or []
            for ci, cat in enumerate(cats[:3], 1):
                curl = category_url_from_seed(seed, cat["alternativeCategoryUrl"])
                cf, chtml = fetch(curl, f"seed_{si}_cat_{ci}")
                cp = extract_page(chtml)
                ctest = {"category": cat, "url": curl, "fetch": cf, "parsed": cp, "pagination_tests": []}
                if cf.get("status") == 200 and cp.get("valid_next_data") and cp.get("product_count", 0) > 0:
                    category_any = True
                    products_any = True
                    prices_any |= cp.get("positive_price_count", 0) > 0
                    unit_refs_any |= cp.get("unit_reference_count", 0) > 0
                    ctest["pagination_tests"] = test_pagination(curl, cp, f"seed_{si}_cat_{ci}")
                    if any(x.get("pagination_validated") for x in ctest["pagination_tests"]):
                        pagination_any = True
                seed_rec["category_tests"].append(ctest)
                time.sleep(2)

        report["seeds"].append(seed_rec)
        time.sleep(2)

    report["summary"] = {
        "structured_products_validated": products_any,
        "structured_positive_prices_validated": prices_any,
        "structured_unit_references_validated": unit_refs_any,
        "structured_categories_validated": category_any,
        "pagination_validated": pagination_any,
    }

    if products_any and prices_any and category_any and pagination_any:
        verdict = "DIRECT_HTTP_NEXTDATA_CATALOG_TRAVERSAL_VALIDATED"
        next_action = "BUILD_FAMILA_HTTP_UPDATER_V1_USING_NEXT_DATA"
    elif products_any and prices_any and category_any:
        verdict = "NEXTDATA_PRODUCTS_PRICES_CATEGORIES_VALIDATED_PAGINATION_PENDING"
        next_action = "REVIEW_PAGINATION_ARTIFACT_ONLY"
    elif products_any and prices_any:
        verdict = "NEXTDATA_PRODUCTS_AND_PRICES_VALIDATED_TRAVERSAL_PENDING"
        next_action = "REVIEW_CATEGORY_AND_PAGINATION_ARTIFACT"
    else:
        verdict = "NEXTDATA_MECHANISM_NOT_SUFFICIENTLY_VALIDATED"
        next_action = "STOP_AND_REVIEW"

    report["summary"]["verdict"] = verdict
    report["summary"]["next_action"] = next_action

    (OUT / "famila_nextdata_probe_v5.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# RISULTATO FAMILA NEXTDATA PROBE V5",
        "",
        f"**Verdetto:** `{verdict}`",
        "",
        f"- Prodotti strutturati: **{products_any}**",
        f"- Prezzi positivi strutturati: **{prices_any}**",
        f"- Prezzi/unità di riferimento strutturati: **{unit_refs_any}**",
        f"- Categorie strutturate: **{category_any}**",
        f"- Paginazione validata: **{pagination_any}**",
        "",
        f"**Prossima azione:** `{next_action}`",
        "",
        "Il dettaglio completo è in `famila_nextdata_probe_v5.json`.",
    ]
    (OUT / "RISULTATO.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
