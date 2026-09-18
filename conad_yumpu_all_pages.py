import requests, os, json
from pathlib import Path

DOCS = [
    ("spunta", 71259088, "volantino-spunta-il-risparmio.jpg", 7, 1130, 1600),
    ("integrativa", 71283848, "volantino-convenienza-piu.jpg", 8, 1250, 1460),
]

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://volantini.conad.it/",
})

Path("yumpu_pages").mkdir(exist_ok=True)
out = []

for name, doc, title, pages, w, h in DOCS:
    for page in range(1, pages + 1):
        url = f"https://img.yumpu.com/{doc}/{page}/{w}x{h}/{title}"
        r = s.get(url, timeout=45)
        row = {
            "doc": name,
            "page": page,
            "url": url,
            "status": r.status_code,
            "content_type": r.headers.get("content-type"),
            "bytes": len(r.content),
        }
        if r.ok and "image" in (r.headers.get("content-type") or ""):
            path = Path("yumpu_pages") / f"{name}_{page:02d}.jpg"
            path.write_bytes(r.content)
            row["file"] = str(path)
        out.append(row)

Path("conad_yumpu_pages_manifest.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(json.dumps({
    "downloaded": sum(1 for x in out if x.get("file")),
    "expected": sum(x[3] for x in DOCS),
    "failures": [x for x in out if not x.get("file")],
}, ensure_ascii=False))
