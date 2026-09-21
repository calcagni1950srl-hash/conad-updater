import json, re, requests, fitz
from pathlib import Path

URL="https://www.conad.it/assets/common/volantini/pac/v2026-/2026_20_Superstore_Campania.pdf"
r=requests.get(URL,timeout=120)
r.raise_for_status()
doc=fitz.open(stream=r.content,filetype="pdf")
pages=[]
for i in range(min(4,len(doc))):
    text=doc[i].get_text("text")
    lines=[re.sub(r"\s+"," ",x).strip() for x in text.splitlines() if x.strip()]
    hits=[x for x in lines if re.search(r"VALID|SETTEMBRE|OTTOBRE|DAL|FINO|OFFERTA|DOMENICA|LUNED|MARTED|MERCOLED|GIOVED|VENERD|SABATO",x,re.I)]
    pages.append({"page":i+1,"lines":lines[:180],"hits":hits})
out={"url":URL,"pages_count":len(doc),"pages":pages}
Path("qa_v81/flyer20_text_probe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"pages":len(doc),"hits":[p["hits"] for p in pages]},ensure_ascii=False))
