from PIL import Image, ImageDraw
from pathlib import Path
import base64, textwrap

SRC=Path("probe_gourmet_pages")
thumbs=[]
for i in range(1,21):
    im=Image.open(SRC/f"page_{i:02d}.jpg").convert("RGB")
    w=260
    h=round(im.height*w/im.width)
    im=im.resize((w,h))
    canvas=Image.new("RGB",(w,h+26),"white")
    canvas.paste(im,(0,26))
    ImageDraw.Draw(canvas).text((8,6),f"PAGINA {i}",fill="black")
    thumbs.append(canvas)

cols=4
rows=5
cell_w=max(x.width for x in thumbs)
cell_h=max(x.height for x in thumbs)
sheet=Image.new("RGB",(cols*cell_w,rows*cell_h),"white")
for idx,im in enumerate(thumbs):
    x=(idx%cols)*cell_w
    y=(idx//cols)*cell_h
    sheet.paste(im,(x,y))
out=SRC/"overview_20_pages.jpg"
sheet.save(out,quality=48,optimize=True)
data=base64.b64encode(out.read_bytes()).decode("ascii")
(SRC/"overview_20_pages.b64.txt").write_text("\n".join(textwrap.wrap(data,4000)),encoding="ascii")
print({"size":out.stat().st_size,"b64_lines":len(textwrap.wrap(data,4000)),"dimensions":sheet.size})
