import requests,json
docs=[
 ("spunta",71259088,"volantino-spunta-il-risparmio.jpg",7,452,640,1130,1600),
 ("integrativa",71283848,"volantino-convenienza-piu.jpg",8,500,584,1250,1460),
]
s=requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://volantini.conad.it/"})
out=[]
for name,doc,title,pages,sw,sh,bw,bh in docs:
  candidates=[
    f"https://img.yumpu.com/{doc}/1/{bw}x{bh}/{title}",
    f"https://img.yumpu.com/{doc}/1/{sw}x{sh}/{title}",
    f"https://img.yumpu.com/{doc}/00000001.jpg",
    f"https://img.yumpu.com/{doc}/1/500x640/{title}",
  ]
  for u in candidates:
    try:
      r=s.get(u,timeout=30)
      out.append({"name":name,"url":u,"status":r.status_code,"content_type":r.headers.get("content-type"),"bytes":len(r.content)})
      if r.ok and "image" in (r.headers.get("content-type") or ""):
        open(f"{name}_page1.jpg","wb").write(r.content)
        break
    except Exception as e:
      out.append({"name":name,"url":u,"error":repr(e)})
print(json.dumps(out,ensure_ascii=False))
