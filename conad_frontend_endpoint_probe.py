import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BASE = "https://spesaonline.conad.it/"
UA = "SmartCampaniaPublicEndpointAudit/1.0"
KEYWORDS = [
    "set-ecaccess",
    "pointOfServiceId",
    "pointofserviceid",
    "storeSelected",
    "gpGetProtectionToken",
    "search.loader",
    "/api/ecommerce/",
    "becommerce",
    "lsfCommerce",
]


def get_text(url, timeout=30):
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, raw.decode("utf-8", "replace"), r.headers.get("content-type", "")


def snippets(text, keyword, radius=500, limit=12):
    low = text.lower()
    needle = keyword.lower()
    out = []
    pos = 0
    while len(out) < limit:
        i = low.find(needle, pos)
        if i < 0:
            break
        a = max(0, i - radius)
        b = min(len(text), i + len(keyword) + radius)
        out.append(text[a:b].replace("\n", " ")[:1400])
        pos = i + max(1, len(keyword))
    return out


def main():
    out = {"base": BASE, "scripts": [], "matches": [], "errors": [], "endpoint_candidates": []}
    try:
        status, html, ctype = get_text(BASE)
        out["home_status"] = status
        out["home_content_type"] = ctype
    except Exception as exc:
        out["errors"].append({"url": BASE, "error": repr(exc)})
        Path("conad_frontend_endpoint_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, flags=re.I)
    urls = []
    seen = set()
    for src in srcs:
        u = urljoin(BASE, src)
        if u in seen:
            continue
        seen.add(u)
        host = urlparse(u).netloc.lower()
        if host.endswith("conad.it"):
            urls.append(u)

    out["script_count"] = len(urls)
    endpoint_re = re.compile(r'["\'](\/(?:api|search)[^"\']{1,220})["\']', re.I)

    for u in urls[:100]:
        item = {"url": u}
        try:
            status, text, ctype = get_text(u)
            item.update({"status": status, "bytes": len(text), "content_type": ctype})
            found = []
            for keyword in KEYWORDS:
                ss = snippets(text, keyword)
                if ss:
                    found.append({"keyword": keyword, "snippets": ss})
            if found:
                out["matches"].append({"url": u, "found": found})
            for ep in endpoint_re.findall(text):
                ep = ep.replace("\\/", "/")
                if ep not in out["endpoint_candidates"]:
                    out["endpoint_candidates"].append(ep)
        except Exception as exc:
            item["error"] = repr(exc)
        out["scripts"].append(item)

    # Keep the report bounded but include enough endpoint candidates for inspection.
    out["endpoint_candidates"] = out["endpoint_candidates"][:500]
    Path("conad_frontend_endpoint_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "home_status": out.get("home_status"),
        "script_count": out.get("script_count"),
        "matched_scripts": len(out["matches"]),
        "endpoint_candidates": out["endpoint_candidates"],
        "errors": out["errors"] + [x for x in out["scripts"] if x.get("error")][:10],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
