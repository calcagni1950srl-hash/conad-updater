from pathlib import Path
import requests, fitz, math, json

OUT=Path("qa_v81_visual")
OUT.mkdir(exist_ok=True)

FLYERS={
 "integrativa19":"https://www.conad.it/assets/common/volantini/pac/vinteg/Integrativa%2019%20Campania.pdf",
 "spunta4":"https://www.conad.it/assets/common/volantini/pac/v2026-/2026-4-spunta-campania.pdf",
 "gourmet3":"https://www.conad.it/assets/common/volantini/pac/vcatal/Catalogo%20Gourmet%20Campania.pdf",
}

summary={}
for key,url in FLYERS.items():
    pdf=OUT/f"{key}.pdf"
    r=requests.get(url,timeout=120)
    r.raise_for_status()
    pdf.write_bytes(r.content)
    doc=fitz.open(pdf)
    thumbs=[]
    for i,page in enumerate(doc):
        pix=page.get_pixmap(matrix=fitz.Matrix(1.15,1.15),alpha=False)
        img=fitz.Pixmap(pix)
        png=OUT/f"{key}_p{i+1:02d}.png"
        pix.save(png)
        thumbs.append(png)
    summary[key]={"pages":len(thumbs),"url":url}

(OUT/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
print(json.dumps(summary,indent=2))
