# -*- coding: utf-8 -*-
"""
Отложенный тестовый набор (дополнение № 2, 04.11.2026) -> corpus/synth_holdout/.
Тот же генератор gen_synth.py, новый seed 20261104, 30 сделок + договоры-помехи.
Идентификаторы с префиксом hold_ (не пересекаются с synth_). Связи только к договору.
Весь набор — split=test; на нём ничего не настраивается, конфигурация замораживается до прогона.
Запуск: python gen_holdout.py [число_сделок] [seed]
"""
import csv, shutil, sys, tempfile
from pathlib import Path

N = sys.argv[1] if len(sys.argv) > 1 else "30"
SEED = sys.argv[2] if len(sys.argv) > 2 else "20261104"
sys.argv = [sys.argv[0], N, SEED]
import gen_synth as g

OUT = Path(__file__).parent / "corpus" / "synth_holdout"
tmp = Path(tempfile.mkdtemp())
g.OUT = tmp
g.main()

def ren(s): return s.replace("synth_", "hold_")
if OUT.exists(): shutil.rmtree(OUT)
OUT.mkdir(parents=True)
for d in tmp.iterdir():
    if d.is_dir(): shutil.copytree(d, OUT / ren(d.name))
docs = list(csv.DictReader(open(tmp / "documents.csv", encoding="utf-8-sig"), delimiter=";"))
links = list(csv.DictReader(open(tmp / "links.csv", encoding="utf-8-sig"), delimiter=";"))
kind = {r["doc_id"]: r["kind"] for r in docs}
links = [l for l in links if kind[l["target"]] == "договор"]   # всё только к договору
for r in docs:
    r["doc_id"], r["deal_id"] = ren(r["doc_id"]), ren(r["deal_id"]); r["split"] = "test"
for l in links: l["source"], l["target"] = ren(l["source"]), ren(l["target"])
for name, rows in (("documents.csv", docs), ("links.csv", links)):
    with open(OUT / name, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";"); w.writeheader(); w.writerows(rows)
shutil.rmtree(tmp)
inp = [r for r in docs if r["kind"] != "договор"]
print(f"документов {len(docs)}, из них входящих {len(inp)}, договоров {len(docs)-len(inp)}, связей {len(links)}, "
      f"входящих с трудными случаями {sum(1 for r in inp if r['hard_types'])}")
