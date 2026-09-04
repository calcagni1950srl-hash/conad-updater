import json, html as htmlmod
from updater_conad_auto import parse_products, parse_total
sample="""<b class="results">136 risultati</b>
<div data-product="{&quot;code&quot;:&quot;262867&quot;,&quot;nome&quot;:&quot;Latte UHT Conad 1 L&quot;,&quot;basePrice&quot;:0.89,&quot;netQuantity&quot;:1,&quot;netQuantityUm&quot;:&quot;LT&quot;}"></div>"""
assert parse_total(sample)==136
p=parse_products(sample)
assert p["262867"]["basePrice"]==0.89
print("OK parser endpoint")

assert parse_total('<b class="results">1 risultato</b>') == 1
assert parse_total('<b class="results">2 risultati</b>') == 2
print("OK totale singolare/plurale")
