# -*- coding: utf-8 -*-
"""
Запуск моделей на корпусе (выполняется у пользователя). Шаг 1 эксперимента главы 2.

Делает две вещи и сохраняет результаты в папку results/:
  1) эмбеддинги BGE-M3 для всех документов (через Ollama)       -> results/emb_bge-m3.json
  2) извлечение реквизитов языковой моделью по JSON-схеме         -> results/extract_<имя>.jsonl
Ответы кэшируются: при обрыве повторный запуск продолжит с места остановки.
Затем папку results/ нужно заархивировать и прислать — расчёт метрик делает linker.py.

Примеры:
  python run_models.py embed
  python run_models.py extract --name lightning --backend ollama --model hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:Q4_K_M
  python run_models.py extract --name qwen     --backend yandex --model "gpt://<folder_id>/<qwen-model>/latest"
  python run_models.py extract --name yandexgpt --backend yandex --model "gpt://<folder_id>/yandexgpt/latest"
Ключ Yandex Cloud берётся из переменных окружения YC_API_KEY и YC_FOLDER_ID.
"""
import argparse, csv, json, os, re, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent / "corpus"
RES = Path(__file__).resolve().parent / "results"
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
YANDEX = "https://llm.api.cloud.yandex.net/v1/chat/completions"

PROMPT = """Ты — помощник бухгалтера. Извлеки реквизиты из текста документа договорного процесса.
Наша организация (клиент бухгалтерии): {client}. Контрагент — другая сторона документа.
Текст мог быть получен распознаванием скана и содержать ошибки (О/0, З/3), исправлять их не нужно: пиши как в тексте.
Ответь ТОЛЬКО объектом JSON по схеме:
{{
 "doc_kind": "договор" | "допсоглашение" | "счёт" | "акт" | "УПД" | "другое",
 "number": "номер самого документа или null",
 "date": "дата самого документа ДД.ММ.ГГГГ или null",
 "counterparty_name": "наименование контрагента или null",
 "counterparty_inn": "ИНН контрагента или null",
 "amount": число — итоговая сумма документа с НДС, или null,
 "subject": "предмет договора или наименование услуг/товаров кратко, до 15 слов",
 "contract_refs": [{{"number": "номер договора-основания или null", "date": "ДД.ММ.ГГГГ или null", "quote": "точная цитата из текста"}}],
 "supp_ref": {{"number": "номер доп. соглашения, на которое ссылается документ, или null", "date": "ДД.ММ.ГГГГ или null", "quote": "цитата"}} или null,
 "invoice_ref": {{"number": "номер счёта, на который ссылается документ, или null", "date": "ДД.ММ.ГГГГ или null", "quote": "цитата"}} или null,
 "evidence": {{"number": "цитата", "date": "цитата", "counterparty": "цитата", "amount": "цитата"}}
}}
Если документ сам является договором, contract_refs — пустой список. Если ссылки на договор нет, тоже пустой список.

ТЕКСТ ДОКУМЕНТА:
{text}"""


def load_docs():
    docs = []
    for part, ckey in (("real", "client"), ("synth", "client_inn")):
        with open(ROOT / part / "documents.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f, delimiter=";"):
                p = ROOT / part / (r["doc_id"] + ".txt")
                docs.append({"id": f"{part}/{r['doc_id']}", "client": r[ckey], "text": p.read_text(encoding="utf-8")})
    return docs


def clip(text, head=6000, tail=2500):
    return text if len(text) <= head + tail else text[:head] + "\n[...]\n" + text[-tail:]


def chat(backend, model, prompt):
    msgs = [{"role": "user", "content": prompt}]
    if backend == "ollama":
        r = requests.post(f"{OLLAMA}/v1/chat/completions", timeout=600,
                          json={"model": model, "messages": msgs, "temperature": 0, "response_format": {"type": "json_object"}})
    else:
        r = requests.post(YANDEX, timeout=300, json={"model": model, "messages": msgs, "temperature": 0},
                          headers={"Authorization": f"Api-Key {os.environ['YC_API_KEY']}",
                                   "x-folder-id": os.environ.get("YC_FOLDER_ID", "")})
    r.raise_for_status()
    j = r.json()
    return j["choices"][0]["message"]["content"], j.get("usage", {})


def parse_json(s):
    s = re.sub(r"^```(?:json)?|```$", "", s.strip(), flags=re.M)
    m = re.search(r"\{.*\}", s, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def extract(a):
    RES.mkdir(exist_ok=True)
    out = RES / f"extract_{a.name}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.open(encoding="utf-8") if l.strip()}
    docs = [d for d in load_docs() if d["id"] not in done]
    print(f"{a.name}: осталось {len(docs)} документов")
    with out.open("a", encoding="utf-8") as f:
        for i, d in enumerate(docs, 1):
            t0 = time.time()
            try:
                raw, usage = chat(a.backend, a.model, PROMPT.format(client=d["client"], text=clip(d["text"])))
                err = None
            except Exception as e:
                raw, usage, err = "", {}, str(e)[:300]
            rec = {"id": d["id"], "model": a.model, "backend": a.backend, "seconds": round(time.time() - t0, 2),
                   "usage": usage, "raw": raw, "parsed": parse_json(raw), "error": err,
                   "date_run": time.strftime("%Y-%m-%d %H:%M")}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            print(f"[{i}/{len(docs)}] {d['id']} {rec['seconds']} с" + (f" ОШИБКА: {err}" if err else ""))
            if err and "401" in err:
                sys.exit("Ошибка авторизации: проверьте YC_API_KEY и YC_FOLDER_ID")


def embed(a):
    RES.mkdir(exist_ok=True)
    docs = load_docs(); vec = {}
    for i in range(0, len(docs), 8):
        batch = docs[i:i + 8]
        r = requests.post(f"{OLLAMA}/api/embed", timeout=600,
                          json={"model": a.model, "input": [clip(d["text"], 3000, 1000) for d in batch]})
        r.raise_for_status()
        for d, v in zip(batch, r.json()["embeddings"]):
            vec[d["id"]] = v
        print(f"эмбеддинги: {min(i + 8, len(docs))}/{len(docs)}")
    (RES / "emb_bge-m3.json").write_text(json.dumps(vec), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["embed", "extract"])
    ap.add_argument("--name", default="model")
    ap.add_argument("--backend", choices=["ollama", "yandex"], default="ollama")
    ap.add_argument("--model", default="bge-m3")
    a = ap.parse_args()
    embed(a) if a.step == "embed" else extract(a)
