import json
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from pypdf import PdfReader

STORE_ID = "010548"
API = f"https://www.conad.it/api/corporate/it-it.getPointOfServiceByAnacanId.json?anacanId={STORE_ID}"
KEY_TERMS = [
    "pasta", "riso", "pane", "farina", "latte", "uova", "formaggio", "mozzarella",
    "provola", "pomodoro", "passata", "pelati", "olio", "aglio", "cipolla", "patate",
    "zucchine", "melanzane", "peperoni", "scarola", "friarielli", "piselli", "fagioli",
    "ceci", "manzo", "pollo", "salsiccia", "prosciutto", "salame", "tonno", "alici",
    "baccal", "cozze", "polpo", "pangrattato", "capperi", "olive", "basilico",
    "prezzemolo", "origano", "lievito", "burro", "ricotta", "parmigiano", "pecorino",
    "limone", "carota", "sedano", "vongole", "gamber", "calamari", "orata", "branzino"
]
PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}[,.]\d{2})(?!\d)")

TARGET_CONTEXT_TERMS = [
    "cavolfiore", "verza", "rosmarino", "origano", "scarola", "sedano",
    "peperoncino", "pepe", "basilico", "vongole", "colatura", "fagiolini",
    "limone", "gamber",
]


def millis_now():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def find_flyers(payload):
    found = []
    seen = set()
    for node in walk(payload):
        pdf = node.get("pdfUrl")
        title = node.get("title") or node.get("feTitle")
        if not pdf or not title:
            continue
        key = (title, pdf)
        if key in seen:
            continue
        seen.add(key)
        found.append({
            "title": title,
            "pdfUrl": pdf,
            "validFrom": node.get("validFrom"),
            "validTo": node.get("validTo"),
            "visible": node.get("visible"),
            "valid": node.get("valid"),
            "mainFlyer": node.get("mainFlyer"),
            "promotions": node.get("promotions"),
            "catalogue": node.get("catalogue"),
            "hasDisaggregated": node.get("hasDisaggregated"),
            "disTotalProducts": node.get("disTotalProducts"),
            "rawKeys": sorted(node.keys()),
            "rawNode": node,
        })
    return found


def is_active(flyer, now_ms):
    start = flyer.get("validFrom")
    end = flyer.get("validTo")
    if flyer.get("valid") is False or flyer.get("visible") is False:
        return False
    if isinstance(start, (int, float)) and now_ms < start:
        return False
    if isinstance(end, (int, float)) and now_ms > end:
        return False
    return True


def extract_pdf(session, flyer):
    r = session.get(flyer["pdfUrl"], timeout=90)
    r.raise_for_status()
    if "pdf" not in (r.headers.get("content-type") or "").lower() and not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"not a PDF: {flyer['pdfUrl']}")
    reader = PdfReader(BytesIO(r.content))
    pages = []
    all_text = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        compact = re.sub(r"\s+", " ", text).strip()
        all_text.append(compact)
        pages.append({
            "page": i,
            "chars": len(compact),
            "price_tokens": len(PRICE_RE.findall(compact)),
        })
    joined = "\n".join(all_text).lower()
    target_contexts = {}
    for page_no, page_text in enumerate(all_text, start=1):
        low = page_text.lower()
        for term in TARGET_CONTEXT_TERMS:
            start = 0
            while True:
                idx = low.find(term, start)
                if idx < 0:
                    break
                left = max(0, idx - 180)
                right = min(len(page_text), idx + len(term) + 220)
                target_contexts.setdefault(term, []).append({
                    "page": page_no,
                    "context": page_text[left:right],
                })
                start = idx + len(term)
    term_hits = {}
    for term in KEY_TERMS:
        count = joined.count(term)
        if count:
            term_hits[term] = count
    return {
        "bytes": len(r.content),
        "pages": len(reader.pages),
        "text_chars": sum(len(x) for x in all_text),
        "price_tokens": len(PRICE_RE.findall(joined)),
        "term_hits": term_hits,
        "target_contexts": target_contexts,
        "page_stats": pages,
        "text_sample": "\n".join(all_text)[:12000],
    }


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 SmartCampania/1.0",
        "Accept-Language": "it-IT,it;q=0.9",
    })
    api_resp = session.get(API, timeout=45)
    api_resp.raise_for_status()
    payload = api_resp.json()
    now_ms = millis_now()
    flyers = find_flyers(payload)
    active = [f for f in flyers if is_active(f, now_ms)]

    out = {
        "store_id": STORE_ID,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "flyers_total": len(flyers),
        "active_flyers": [],
        "all_flyer_meta": flyers,
        "errors": [],
    }

    for flyer in active:
        item = dict(flyer)
        try:
            item["pdf_extract"] = extract_pdf(session, flyer)
        except Exception as exc:
            item["error"] = repr(exc)
            out["errors"].append({"title": flyer["title"], "error": repr(exc)})
        out["active_flyers"].append(item)
        time.sleep(1)

    union_terms = set()
    total_prices = 0
    total_chars = 0
    for item in out["active_flyers"]:
        ex = item.get("pdf_extract") or {}
        union_terms.update((ex.get("term_hits") or {}).keys())
        total_prices += ex.get("price_tokens") or 0
        total_chars += ex.get("text_chars") or 0
    out["summary"] = {
        "active_count": len(out["active_flyers"]),
        "total_text_chars": total_chars,
        "total_price_tokens": total_prices,
        "key_terms_covered": len(union_terms),
        "key_terms_total": len(KEY_TERMS),
        "covered_terms": sorted(union_terms),
        "missing_terms": sorted(set(KEY_TERMS) - union_terms),
    }

    Path("conad_capodrise_active_flyers_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "store_id": STORE_ID,
        "flyers_total": out["flyers_total"],
        "active_titles": [x["title"] for x in out["active_flyers"]],
        "summary": out["summary"],
        "errors": out["errors"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
