import json
from pathlib import Path

SRC=Path("conad_v81_android_products.jsonl")
OUT=Path("qa_v81/products_current_20260921.tsv")
rows=[]

for line in SRC.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    x=json.loads(line)
    key=str(x.get("key") or "")
    if key.startswith("FLYER:"):
        continue
    if key.startswith("VISUAL:"):
        continue
    rows.append(x)

rows.extend([
    {
        "key":"VISUAL:SPUNTA2026_ACETO_BIANCO_PONTI_1L",
        "supermarket":"Conad",
        "store":"Conad Superstore Capodrise",
        "name":"Aceto di vino bianco Ponti 1 L",
        "brand":"Ponti",
        "category":"Condimenti e conserve > Aceti > Offerta ufficiale verificata visivamente",
        "quantityValue":1.0,
        "quantityUnit":"L",
        "priceEur":1.19,
        "unitPriceEur":1.19,
        "unitPriceUnit":"litro",
        "variableWeight":False,
        "sourceUrl":"OFFICIAL_FLYER_VISUAL_VERIFIED_V81|SPUNTA_RISPARMIO_CAMPANIA|NO_OCR",
        "checkedAt":"2026-09-21T00:00:00Z",
    },
    {
        "key":"VISUAL:INTEGRATIVA19_MALDON_SALE_MARINO_125G",
        "supermarket":"Conad",
        "store":"Conad Superstore Capodrise",
        "name":"Sale marino Maldon 125 g",
        "brand":"Maldon",
        "category":"Condimenti e conserve > Sale e spezie > Offerta ufficiale verificata visivamente",
        "quantityValue":0.125,
        "quantityUnit":"KG",
        "priceEur":3.19,
        "unitPriceEur":25.52,
        "unitPriceUnit":"kg",
        "variableWeight":False,
        "sourceUrl":"OFFICIAL_FLYER_VISUAL_VERIFIED_V81|CONVENIENZA_PIU_INTEGRATIVA19_CAMPANIA|NO_OCR",
        "checkedAt":"2026-09-21T00:00:00Z",
    },
])

def clean(v):
    if v is None:
        return ""
    return str(v).replace("\t"," ").replace("\r"," ").replace("\n"," ")

with OUT.open("w",encoding="utf-8") as fh:
    fh.write("key\tname\tbrand\tcategory\tquantityValue\tquantityUnit\tpriceEur\tunitPriceEur\tunitPriceUnit\tvariableWeight\tsourceUrl\tcheckedAt\n")
    for x in rows:
        fh.write("\t".join([
            clean(x.get("key")),
            clean(x.get("name")),
            clean(x.get("brand")),
            clean(x.get("category")),
            clean(x.get("quantityValue")),
            clean(x.get("quantityUnit")),
            clean(x.get("priceEur")),
            clean(x.get("unitPriceEur")),
            clean(x.get("unitPriceUnit")),
            "1" if x.get("variableWeight") else "0",
            clean(x.get("sourceUrl")),
            clean(x.get("checkedAt")),
        ])+"\n")

summary={
    "products":len(rows),
    "flyer_rows":sum(1 for x in rows if str(x.get("key") or "").startswith("FLYER:")),
    "visual_rows":sum(1 for x in rows if str(x.get("key") or "").startswith("VISUAL:")),
    "reference_rows":sum(1 for x in rows if str(x.get("key") or "").startswith("REF:")),
}
Path("qa_v81/current_projection_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
print(json.dumps(summary))
