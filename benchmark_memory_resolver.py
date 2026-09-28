import json,subprocess,sys
DB=sys.argv[1] if len(sys.argv)>1 else "prezzi_confronto_fast.db"
tests=[
["carta igienica","prosciutto crudo 200 g","zucchine 1 kg","acqua Lete 6 bottiglie","penne Barilla 500 g"],
["carta wc","crudo 200 grammi","rotoli bagno","acqua minerale naturale 6 bottiglie","pasta Barilla 500 g"]]
for t in tests:
 p=subprocess.run([sys.executable,"server_resolver_memory.py",DB,*t],capture_output=True,text=True,check=True)
 d=json.loads(p.stdout)
 print("TEST",t)
 for mode in ("cheapest","same_brand"):
  x=d[mode]; print("MODE",mode,"LOAD",x["load_ms"],"ELAPSED",x["elapsed_ms"])
  for i in x["items"]: print(i["request"],i["product_type"],i["requested_brand"],{m:(v["name"] if v else None) for m,v in i["markets"].items()})
base=["pasta","riso","latte","uova","burro","olio","farina","zucchero","sale","pane","pomodori","patate","zucchine","cipolle","carote","mele","banane","yogurt","mozzarella","parmigiano","prosciutto crudo","salame","tonno","salmone","acqua","coca cola","succo","biscotti","caffe","carta igienica","carta cucina","detersivo lavatrice","ammorbidente","detersivo piatti","shampoo","dentifricio","sapone","deodorante","fazzoletti","tovaglioli","fagioli","ceci","lenticchie","piselli","mais","pollo","manzo","merluzzo","gelato","pizza"]
for n in (10,25,50):
 p=subprocess.run([sys.executable,"server_resolver_memory.py",DB,*base[:n]],capture_output=True,text=True,check=True)
 d=json.loads(p.stdout)["cheapest"]
 found=sum(v is not None for i in d["items"] for v in i["markets"].values())
 print("BATCH",n,"LOAD",d["load_ms"],"ELAPSED",d["elapsed_ms"],"FOUND",found,"/",n*6)
