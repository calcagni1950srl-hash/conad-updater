import fitz, requests
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw

URL="https://www.conad.it/assets/common/volantini/pac/vcatal/Catalogo%20Gourmet%20Campania.pdf?_u=4f2d13440e5d918584ad7a09ddaa5c34d2be3c31"
OUT=Path("probe_gourmet_pages")
OUT.mkdir(exist_ok=True)

r=requests.get(URL, timeout=120)
r.raise_for_status()
pdf=fitz.open(stream=r.content, filetype="pdf")

page_paths=[]
for i,page in enumerate(pdf,1):
    pix=page.get_pixmap(matrix=fitz.Matrix(1.6,1.6), alpha=False)
    p=OUT/f"page_{i:02d}.jpg"
    pix.save(str(p))
    page_paths.append(p)

# 4 contact sheets, five pages each, preserving enough resolution for visual inspection.
for group in range(4):
    items=[]
    for idx in range(group*5, min(group*5+5, len(page_paths))):
        im=Image.open(page_paths[idx]).convert("RGB")
        if im.width>1200:
            h=round(im.height*1200/im.width)
            im=im.resize((1200,h))
        canvas=Image.new("RGB",(im.width,im.height+60),"white")
        canvas.paste(im,(0,60))
        d=ImageDraw.Draw(canvas)
        d.text((20,15),f"PAGE {idx+1}",fill="black")
        items.append(canvas)
    w=max(x.width for x in items)
    h=sum(x.height for x in items)
    sheet=Image.new("RGB",(w,h),"white")
    y=0
    for im in items:
        sheet.paste(im,(0,y)); y+=im.height
    sheet.save(OUT/f"contact_{group*5+1:02d}_{group*5+len(items):02d}.jpg",quality=82,optimize=True)

print({"pages":len(page_paths),"bytes":len(r.content)})
