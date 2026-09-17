import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from pypdf import PdfReader

STORE_ID = "010548"
API = f"https://www.conad.it/api/corporate/it-it.getPointOfServiceByAnacanId.json?anacanId={STORE_ID}"
TARGET_TITLE = "Campioni del Risparmio"
PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}[,.]\d{2})\s*€")


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def active_flyer(payload):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    for node in walk(payload):
        if node.get("title") != TARGET_TITLE or not node.get("pdfUrl"):
            continue
        if node.get("visible") is False or node.get("valid") is False:
            continue
        start, end = node.get("validFrom"), node.get("validTo")
        if isinstance(start, (int, float)) and now < start:
            continue
        if isinstance(end, (int, float)) and now > end:
            continue
        return node
    raise RuntimeError("active flyer not found")


def blocks_around_prices(text):
    lines = [ln.rstrip() for ln in text.splitlines()]
    out = []
    for i, line in enumerate(lines):
        prices = PRICE_RE.findall(line)
        if not prices:
            continue
        lo, hi = max(0, i-3), min(len(lines), i+4)
        context = "\n".join(lines[lo:hi]).strip()
        out.append({"line": i+1, "prices": prices, "context": context})
    return out


def main():
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 SmartCampania/1.0","Accept-Language":"it-IT,it;q=0.9"})
    payload = s.get(API, timeout=45).json()
    flyer = active_flyer(payload)
    r = s.get(flyer["pdfUrl"], timeout=90)
    r.raise_for_status()
    reader = PdfReader(BytesIO(r.content))

    result = {
        "store_id": STORE_ID,
        "title": TARGET_TITLE,
        "pdf_url": flyer["pdfUrl"],
        "validFrom": flyer.get("validFrom"),
        "validTo": flyer.get("validTo"),
        "pages": [],
    }
    total_blocks = 0
    for n, page in enumerate(reader.pages, start=1):
        normal = page.extract_text() or ""
        try:
            layout = page.extract_text(extraction_mode="layout") or ""
        except Exception as exc:
            layout = normal
            layout_error = repr(exc)
        else:
            layout_error = None
        blocks = blocks_around_prices(layout)
        total_blocks += len(blocks)
        result["pages"].append({
            "page": n,
            "normal_chars": len(normal),
            "layout_chars": len(layout),
            "layout_error": layout_error,
            "price_blocks": blocks,
            "layout_text": layout,
        })

    result["summary"] = {
        "page_count": len(result["pages"]),
        "price_blocks": total_blocks,
        "pages_with_prices": sum(1 for p in result["pages"] if p["price_blocks"]),
    }
    Path("conad_capodrise_layout_probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "store_id": STORE_ID,
        "title": TARGET_TITLE,
        "summary": result["summary"],
        "examples": [b for p in result["pages"] for b in p["price_blocks"]][:12],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
