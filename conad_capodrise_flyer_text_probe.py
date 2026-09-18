import html, json, re, requests
from pathlib import Path
import fitz

STORE_URL="https://www.conad.it/ricerca-negozi/conad-superstore-via-retella-ex-giarddel-sole-snc-81020-capodrise--010548"
OUT={"pdfs":[],"errors":[]}

s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"})
r=s.get(STORE_URL,timeout=60)
r.raise_for_status()
page=html.unescape(r.text)
urls=[]
for u in re.findall(r'https://www\.conad\.it/assets/common/volantini/[^"\s<]+?\.pdf(?:\?[^"\s<]*)?',page,re.I):
    u=u.replace('&amp;','&')
    if u not in urls: urls.append(u)

selected=[]
seen_paths=set()
for u in urls:
    low=u.lower()
    if any(k in low for k in ("superstore_campania","integrativa%2019%20campania","integrativa 19 campania","catalogo%20gourmet%20campania","2026-4-spunta-campania")):
        key=u.split("?")[0]
        if key not in seen_paths:
            selected.append(u)
            seen_paths.add(key)

Path("flyer_texts").mkdir(exist_ok=True)
for i,u in enumerate(selected,1):
    try:
        rr=s.get(u,timeout=90)
        rr.raise_for_status()
        pdf=fitz.open(stream=rr.content,filetype="pdf")
        parts=[]
        price_tokens=0
        layout_pages=[]
        for n,p in enumerate(pdf,1):
            text=p.get_text("text")
            price_tokens += len(re.findall(r'\b\d{1,3}[,.]\d{2}\s*€',text))
            parts.append(f"\n===== PAGE {n} =====\n{text}")
            if "superstore_campania" in u.lower():
                blocks=[]
                for b in p.get_text("dict").get("blocks",[]):
                    if "lines" not in b:
                        continue
                    spans=[]
                    for line in b.get("lines",[]):
                        for s in line.get("spans",[]):
                            t=(s.get("text") or "").strip()
                            if t:
                                spans.append({
                                    "text":t,
                                    "bbox":[round(v,2) for v in s.get("bbox",[])],
                                    "size":round(float(s.get("size") or 0),2),
                                    "font":s.get("font"),
                                    "flags":s.get("flags")
                                })
                    if spans:
                        blocks.append({"bbox":[round(v,2) for v in b.get("bbox",[])],"spans":spans})
                layout_pages.append({"page":n,"width":round(p.rect.width,2),"height":round(p.rect.height,2),"blocks":blocks})
        txt="".join(parts)
        name=re.sub(r'[^A-Za-z0-9_.-]+','_',u.split('/')[-1].split('?')[0])[:100]+".txt"
        Path("flyer_texts",name).write_text(txt,encoding="utf-8")
        row={"url":u,"pages":len(pdf),"bytes":len(rr.content),"text_len":len(txt),"price_tokens":price_tokens,"txt":name}
        if layout_pages:
            layout_name="superstore_layout.json"
            Path("flyer_texts",layout_name).write_text(json.dumps(layout_pages,ensure_ascii=False,indent=2),encoding="utf-8")
            row["layout"]=layout_name
        OUT["pdfs"].append(row)
    except Exception as e:
        OUT["errors"].append({"url":u,"error":repr(e)})

Path("conad_capodrise_flyer_text_probe.json").write_text(json.dumps(OUT,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(OUT,ensure_ascii=False))
