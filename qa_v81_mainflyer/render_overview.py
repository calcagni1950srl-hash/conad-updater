from pathlib import Path
import requests, fitz, math
from PIL import Image,ImageOps,ImageDraw

URL="https://www.conad.it/assets/common/volantini/pac/v2026-/2026_19_Superstore_Campania.pdf"
OUT=Path("qa_v81_mainflyer")
OUT.mkdir(exist_ok=True)
pdf=OUT/"main.pdf"
r=requests.get(URL,timeout=120); r.raise_for_status(); pdf.write_bytes(r.content)
doc=fitz.open(pdf)
thumbs=[]
for i,page in enumerate(doc):
    pix=page.get_pixmap(matrix=fitz.Matrix(0.55,0.55),alpha=False)
    p=OUT/f"p{i+1:02d}.png"; pix.save(p)
    im=Image.open(p).convert("RGB")
    im.thumbnail((250,340))
    c=Image.new("RGB",(260,370),"white")
    c.paste(im,((260-im.width)//2,10))
    ImageDraw.Draw(c).text((8,348),f"p{i+1:02d}",fill="black")
    thumbs.append(c)
cols=4; rows=math.ceil(len(thumbs)/cols)
sheet=Image.new("RGB",(cols*260,rows*370),"white")
for i,im in enumerate(thumbs): sheet.paste(im,((i%cols)*260,(i//cols)*370))
sheet.save(OUT/"overview.jpg",quality=88)
print("pages",len(thumbs))
