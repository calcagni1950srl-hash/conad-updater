import re, urllib.request, urllib.parse, json
TERMS=["passata di pomodoro","spaghetti","olio extravergine","zucchine"]
for term in TERMS:
    url="https://www.lidl.it/q/query/"+urllib.parse.quote(term)
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        html=r.read().decode("utf-8","ignore")
    print("\n=== TERM",term,"LEN",len(html),"===")
    pats=[
      r".{0,500}ProductGridbox.{0,1500}",
      r".{0,500}fragment\.0b0d5b2b.{0,1500}",
      r".{0,500}/q/api/search.{0,1500}",
      r".{0,500}fetch\(.{0,1500}",
      r".{0,500}xhr.{0,1500}",
      r".{0,500}searchEndpoint.{0,1500}",
      r".{0,500}api/search.{0,1500}"
    ]
    found=set()
    for p in pats:
        for m in re.finditer(p,html,re.I|re.S):
            s=re.sub(r"\s+"," ",m.group(0))
            if s not in found:
                found.add(s); print("SNIP",s[:2200])
    # print suspicious URLs/endpoints
    urls=sorted(set(re.findall(r'https?://[^\\"\'<> ]+|/[A-Za-z0-9_?&=./:%+-]{8,}',html)))
    for u in urls:
        low=u.lower()
        if any(k in low for k in ["productgrid","fragment","search","query","api"]):
            print("URL",u[:1000])
