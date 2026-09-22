import re, urllib.request, xml.etree.ElementTree as ET, json, time
from urllib.parse import urlparse
UA={"User-Agent":"Mozilla/5.0 (compatible; SmartCampaniaLidlQA/1.0)","Accept":"application/xml,text/xml,text/html,*/*"}
ROOT="https://www.lidl.it/static/sitemap.xml"

def get(url):
    req=urllib.request.Request(url,headers=UA)
    with urllib.request.urlopen(req,timeout=45) as r:
        return r.read()

def parse_xml(data):
    root=ET.fromstring(data)
    locs=[]
    for el in root.iter():
        if el.tag.endswith("loc") and el.text:
            locs.append(el.text.strip())
    return root.tag,locs

seen=set(); product_urls=[]; sitemap_urls=[]; queue=[ROOT]
while queue and len(sitemap_urls)<200:
    u=queue.pop(0)
    if u in seen: continue
    seen.add(u)
    try:
        data=get(u)
        tag,locs=parse_xml(data)
    except Exception as e:
        print("SITEMAP_ERROR",u,repr(e))
        continue
    sitemap_urls.append(u)
    print("SITEMAP",len(sitemap_urls),u,"urls",len(locs))
    for loc in locs:
        if re.search(r"/p/[^/]+/p\d+",loc):
            product_urls.append(loc)
        elif loc.endswith(".xml") and "lidl.it" in loc and loc not in seen:
            queue.append(loc)

product_urls=list(dict.fromkeys(product_urls))
print(json.dumps({"sitemaps":len(sitemap_urls),"product_urls":len(product_urls)},ensure_ascii=False))
for u in product_urls[:60]:
    print("PRODUCT_URL",u)

# Recipe staples expected to exist if sitemap exposes evergreen food products.
terms=["passata","spaghetti","olio-extravergine","uova","zucchine","mozzarella","burro","farina","lenticchie","fagioli","pane"]
hits={t:[] for t in terms}
for u in product_urls:
    low=u.lower()
    for t in terms:
        if t in low and len(hits[t])<20:
            hits[t].append(u)
print("TERM_HITS",json.dumps(hits,ensure_ascii=False))
