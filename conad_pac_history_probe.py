import requests, json, re
from pathlib import Path
from CONAD_CAPODRISE_ENGINE import parse_flyer, parse_validity

BASE="https://www.conad.it/assets/common/volantini/pac/v2026-/2026_{:02d}_Superstore_Campania.pdf"
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Accept-Language":"it-IT,it;q=0.9"})
out=[]

for issue in range(10,20):
    url=BASE.format(issue)
    try:
        r=s.get(url,timeout=120)
        row={"issue":issue,"url":url,"status":r.status_code,"bytes":len(r.content)}
        if r.status_code==200 and r.content.startswith(b"%PDF"):
            import fitz
            pdf=fitz.open(stream=r.content,filetype="pdf")
            text="\n".join(p.get_text("text") for p in pdf)
            start,end=parse_validity(text)
            offers,audit=parse_flyer(r.content)
            row.update({
                "pages":len(pdf),
                "valid_from":start.isoformat() if start else None,
                "valid_to":end.isoformat() if end else None,
                "accepted_offers":len(offers),
                "parser":audit,
                "samples":offers[:10],
            })
            Path(f"pac_history_{issue:02d}.json").write_text(
                json.dumps({"offers":offers,"audit":audit,"url":url},ensure_ascii=False,indent=2),
                encoding="utf-8"
            )
        out.append(row)
    except Exception as e:
        out.append({"issue":issue,"url":url,"error":repr(e)})

Path("conad_pac_history_probe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps([{
    "issue":x.get("issue"),"status":x.get("status"),"pages":x.get("pages"),
    "valid_from":x.get("valid_from"),"valid_to":x.get("valid_to"),
    "accepted_offers":x.get("accepted_offers"),"bytes":x.get("bytes"),
    "error":x.get("error")
} for x in out],ensure_ascii=False))
