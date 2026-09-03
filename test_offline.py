from updater_conad_auto import *
from pathlib import Path
H=Path("sample_latte.html").read_text(encoding="utf-8",errors="replace")
assert parse_total(H)==291
assert parse_last_page(H)==8
s=parse_store(H)
assert s["name"]=="010548"
p=parse_products(H)
assert len(p)==40
assert p["262867"]["basePrice"]==0.89
up,u=unit_price(p["404283"])
assert round(up,2)==1.30 and u=="EUR/L"
print("OK offline: store 010548, 291 risultati, 8 pagine, 40 card, €/L corretto.")
