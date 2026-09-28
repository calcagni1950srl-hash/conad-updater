import json, subprocess, sys, time
DB=sys.argv[1] if len(sys.argv)>1 else "prezzi_confronto_fast.db"
CASES=[
 ["carta igienica","prosciutto crudo 200 g","zucchine 1 kg","acqua Lete 6 bottiglie","penne Barilla 500 g"],
 ["carta wc","crudo 200 grammi","rotoli bagno","acqua minerale naturale 6 bottiglie","pasta Barilla 500 g"],
]
for case in CASES:
    p=subprocess.run([sys.executable,"server_resolver.py",DB,*case],capture_output=True,text=True,check=True)
    d=json.loads(p.stdout)
    print("CASE",case)
    print("ELAPSED_MS",d["elapsed_ms"])
    for it in d["items"]:
        print(" ",it["request"],"=>",it["product_type"],{m:(v["name"] if v else None) for m,v in it["markets"].items()})

base=["pasta","riso","latte","uova","burro","olio","farina","zucchero","sale","pane","pomodori","patate","zucchine","cipolle","carote","mele","banane","yogurt","mozzarella","parmigiano","prosciutto crudo","salame","tonno","salmone","acqua","coca cola","succo","biscotti","caffe","carta igienica","carta cucina","detersivo lavatrice","ammorbidente","detersivo piatti","shampoo","dentifricio","sapone","deodorante","fazzoletti","tovaglioli","fagioli","ceci","lenticchie","piselli","mais","pollo","manzo","merluzzo","gelato","pizza"]
for n in (10,25,50):
    items=base[:n]
    p=subprocess.run([sys.executable,"server_resolver.py",DB,*items],capture_output=True,text=True,check=True)
    d=json.loads(p.stdout)
    found=sum(v is not None for it in d["items"] for v in it["markets"].values())
    total=len(d["items"])*6
    print("BATCH",n,"ELAPSED_MS",d["elapsed_ms"],"FOUND",found,"/",total)
