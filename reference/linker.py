# -*- coding: utf-8 -*-
"""
Метод установления связей и расчёт метрик (глава 2, пп. 2.1–2.3).

Методы:
  rules     — базовый: номер договора регулярным выражением, точное совпадение
  semantic  — базовый: ближайший по эмбеддингам документ допустимого вида
  combined  — предлагаемый: извлечение реквизитов (LLM) -> нормализация -> кандидаты
              (блокировка по клиенту и времени + смысловая близость) -> формальные проверки
              -> взвешенная оценка и два порога
Абляции: combined без LLM (реквизиты регулярками), без проверок, без смыслового поиска.

Запуск: python linker.py [--extract results/extract_qwen.jsonl ...] [--emb results/emb_bge-m3.json]
Без файлов результатов работает на регулярках и TF-IDF (для отладки).
"""
import argparse, csv, json, random, re
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent / "corpus"
OUT = Path(__file__).resolve().parent / "results"
# Все документы связываются только с договором (единая сущность сделки)
ALLOWED = {"допсоглашение": {"договор": "изменение"}, "счёт": {"договор": "основание"},
           "акт": {"договор": "исполнение"}, "УПД": {"договор": "исполнение"}}
KINDS = {"договор", "допсоглашение", "счёт", "акт", "УПД"}
# Косвенных путей (через допсоглашение или счёт) в версии 1 нет: каждый документ сопоставляется с договорами напрямую.

# ---------------- данные ----------------
def load():
    docs, gold = {}, set()
    for part, ckey in (("real", "client"), ("synth", "client_inn")):
        with open(ROOT / part / "documents.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r.get("in_scope", "1") == "0":
                    continue
                i = f"{part}/{r['doc_id']}"
                docs[i] = {"id": i, "part": part, "deal": f"{part}/{r['deal_id']}", "client": r[ckey], "kind": r["kind"],
                           "number": r["number"], "date": pdate(r["date"]), "cp_inn": r.get("cp_inn", ""),
                           "amount": float(r["amount"]) if r.get("amount") else None,
                           "hard": {int(x) for x in r["hard_types"].split(";") if x},
                           "text": (ROOT / part / (r["doc_id"] + ".txt")).read_text(encoding="utf-8")}
        with open(ROOT / part / "links.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f, delimiter=";"):
                s, t = f"{part}/{r['source']}", f"{part}/{r['target']}"
                if s in docs and t in docs:
                    gold.add((s, t, r["type"]))
    return docs, gold


def split(docs, tune_share=0.3, seed=2026):
    deals = sorted({d["deal"] for d in docs.values()})
    rnd = random.Random(seed); rnd.shuffle(deals)
    tune = set(deals[: round(len(deals) * tune_share)])
    return {i for i, d in docs.items() if d["deal"] in tune}, {i for i, d in docs.items() if d["deal"] not in tune}

# ---------------- нормализация ----------------
def pdate(s):
    if not s: return None
    s = str(s).strip().replace(",", ".").replace(" ", "")
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try: return datetime.strptime(s, fmt).date()
        except ValueError: pass
    return None

OCR = str.maketrans({"О": "0", "о": "0", "O": "0", "З": "3", "l": "1", "I": "1", "б": "6", "—": "-", "–": "-"})
def nnum(s):
    if not s: return ""
    s = str(s).translate(OCR).upper()
    s = re.sub(r"^№\s*", "", s.strip())
    return re.sub(r"[\s/\\\-_.]", "", s)

def raw_num(s):
    return re.sub(r"^№\s*|\s+", "", str(s or "").strip().upper())

def num_sim(a, b):
    """Сравниваются и сырой, и нормализованный номер: сырое совпадение 1, совпадение после нормализации OCR 0,9."""
    if raw_num(a) and raw_num(a) == raw_num(b): return 1.0
    a, b = nnum(a), nnum(b)
    if not a or not b: return 0.0
    if a == b: return 0.9
    if len(a) >= 3 and (a.startswith(b) or b.startswith(a)): return 0.8
    r = SequenceMatcher(None, a, b).ratio()
    return 0.7 if r >= 0.8 else 0.0

def inn_ok(s):
    s = re.sub(r"\D", "", str(s or ""))
    d = [int(c) for c in s]
    f = lambda k: sum(x * y for x, y in zip(d, k)) % 11 % 10
    if len(d) == 10: return f([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(d) == 12: return f([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10] and f([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0]) == d[11]
    return False

def name_root(s):
    s = re.sub(r"\b(ООО|АО|ПАО|ЗАО|ОАО|АНО|ГУП|ГБУ|ИП|филиал.*)\b", " ", str(s or ""), flags=re.I)
    return re.sub(r"[^а-яёa-z0-9]", "", s.lower())

# ---------------- извлечение без LLM (регулярные выражения) ----------------
KIND_RX = [("допсоглашение", r"ДОПОЛНИТЕЛЬНОЕ\s+СОГЛАШЕНИЕ|СОГЛАШЕНИЕ\s+о\s+расторжении"), ("УПД", r"УНИВЕРСАЛЬНЫЙ\s+ПЕРЕДАТОЧНЫЙ"),
           ("счёт", r"СЧ[ЁЕ]Т\s+НА\s+ОПЛАТУ|^\s*СЧ[ЁЕ]Т\s+№"), ("акт", r"^\s*АКТ\b"), ("договор", r"ДОГОВОР")]
RX_REF = re.compile(r"[Дд]оговор[а-я]*\s+(?:[а-я\- ]{0,60}?)?(?:№\s*([\w/\-.]+)\s*)?(?:от\s*«?(\d{1,2})»?[\s.]*([а-я]+|\d{2})[\s.]*(\d{4}))?", re.I)
MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]

def rx_date(d, m, y):
    try:
        mm = int(m) if m.isdigit() else next(i + 1 for i, p in enumerate(MONTHS) if m.lower().startswith(p))
        return f"{int(d):02d}.{mm:02d}.{y}"
    except Exception:
        return None

def regex_extract(text):
    head = text[:400]
    kind = next((k for k, rx in KIND_RX if re.search(rx, head, re.I | re.M)), "другое")
    m = re.search(r"№\s*([\w/\-.]+)", head)
    dm = re.search(r"«?(\d{1,2})»?\s+(январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)[а-я]*\s+(\d{4})|(\d{2}\.\d{2}\.\d{4})", head)
    date = (dm.group(4) or rx_date(dm.group(1), dm.group(2), dm.group(3))) if dm else None
    refs = []
    if kind != "договор":
        for r in RX_REF.finditer(text):
            if r.group(1) or r.group(2):
                refs.append({"number": r.group(1), "date": rx_date(r.group(2), r.group(3), r.group(4)) if r.group(2) else None})
    inns = re.findall(r"ИНН\s*:?\s*(\d{10}|\d{12})", text)
    sm = re.search(r"(?:Всего к оплате|Цена договора составляет)\s*:?\s*([\d ]+,\d{2})", text)
    supp = re.search(r"дополнительному соглашению\s*№\s*(\w+)", text, re.I)
    inv = re.search(r"сч[её]т\s*№\s*(\w+)\s*от\s*(\d{2}\.\d{2}\.\d{4})", text, re.I)
    return {"doc_kind": kind, "number": m.group(1) if m else None, "date": date,
            "counterparty_inn": inns[1] if len(inns) > 1 else (inns[0] if inns else None), "counterparty_name": None,
            "amount": float(sm.group(1).replace(" ", "").replace(",", ".")) if sm else None,
            "contract_refs": refs[:3], "supp_ref": {"number": supp.group(1)} if supp else None,
            "invoice_ref": {"number": inv.group(1), "date": inv.group(2)} if inv else None}

def load_extract(path, docs):
    ex, meta = {}, {}
    for l in open(path, encoding="utf-8"):
        r = json.loads(l)
        # технический сбой (таймаут, невалидный JSON) — отдельный исход «ошибка», а не «договор не найден»
        ex[r["id"]] = r["parsed"] if (r.get("parsed") and not r.get("error")) else {"_error": True}
        meta[r["id"]] = r
    return {i: ex.get(i, {"_error": True}) for i in docs}, meta

# ---------------- эмбеддинги ----------------
def tfidf_emb(docs):
    from sklearn.feature_extraction.text import TfidfVectorizer
    ids = list(docs)
    m = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=50000).fit_transform([docs[i]["text"][:4000] for i in ids])
    m = m.toarray(); m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    return dict(zip(ids, m))

def load_emb(path, docs):
    v = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for i in docs:
        x = np.array(v[i], dtype=float); out[i] = x / (np.linalg.norm(x) + 1e-9)
    return out

# ---------------- кандидаты ----------------
# ---------------- карточка и кандидаты ----------------
def card_of(ex, docs):
    """Карточка документа заполняется из извлечения (LLM или регулярки); эталон не используется.
    Бухгалтер может исправить карточку, после чего поиск повторяется. Нет вида или даты — документ идёт на разбор."""
    c = {}
    for i in docs:
        e = ex.get(i) or {}
        k = e.get("doc_kind")
        c[i] = {"kind": k if k in KINDS else None, "date": pdate(e.get("date")), "error": bool(e.get("_error"))}
    return c

def pool(docs, card, src):
    """Договоры того же клиента с датой (из карточки) не позже даты документа. Единственное правило дат."""
    out = []
    for t in docs:
        if t == src or docs[t]["client"] != docs[src]["client"] or card[t]["kind"] != "договор": continue
        if card[t]["date"] and card[src]["date"] and card[t]["date"] > card[src]["date"]: continue
        out.append(t)
    return out

def status_of(card, s):
    """Исход до сопоставления: error — технический сбой; review — нет вида/даты; skip — договор (сам не связывается)."""
    if card[s]["error"]: return "error"
    if card[s]["kind"] == "договор": return "skip"
    if card[s]["kind"] not in ALLOWED or not card[s]["date"]: return "review"
    return "ok"

# ---------------- методы ----------------
# Каждый метод возвращает (pred, blocked): pred[(s, t, тип)] = оценка; blocked — пары, которые можно показать
# бухгалтеру как альтернативы, но нельзя выдать готовой рекомендацией ни при каком пороге.

def m_rules(docs, ids, card):
    pred = {}
    for s in ids:
        if status_of(card, s) != "ok": continue
        ex = regex_extract(docs[s]["text"])
        for t in pool(docs, card, s):
            tex = regex_extract(docs[t]["text"])
            if any(r["number"] and raw_num(r["number"]).strip(".") == raw_num(tex["number"]).strip(".") for r in ex["contract_refs"]):
                pred[(s, t, ALLOWED[card[s]["kind"]]["договор"])] = 1.0
    return pred, set()

def m_semantic(docs, ids, emb, card):
    pred = {}
    for s in ids:
        if status_of(card, s) != "ok": continue
        cands = [(t, float(emb[s] @ emb[t])) for t in pool(docs, card, s)]
        if cands:
            t, c = max(cands, key=lambda x: x[1])
            pred[(s, t, ALLOWED[card[s]["kind"]]["договор"])] = c
    return pred, set()

W = {"num": 0.45, "date": 0.15, "inn": 0.15, "name": 0.05, "sem": 0.20}

def ref_list(e):
    """Явные ссылки на договор; номер и дата одной ссылки рассматриваются вместе. Дубли по номеру убираются."""
    out, seen = [], set()
    for r in e.get("contract_refs") or []:
        key = nnum(r.get("number")) or (r.get("date") or "")
        if key and key not in seen:
            seen.add(key); out.append(r)
    return out

def ref_match(r, et):
    n = num_sim(r.get("number"), et.get("number"))
    d, dt = pdate(r.get("date")), pdate(et.get("date"))
    dsc = (1.0 if d == dt else 0.5 if abs((d - dt).days) <= 10 else 0.0) if d and dt else 0.0
    return n, dsc

def features(s, t, ex, emb, use_sem=True):
    es, et = ex[s], ex[t]
    refs = ref_list(es)
    best = max((ref_match(r, et) for r in refs), key=lambda x: (x[0], x[1]), default=(0.0, 0.0))
    f = {"num": best[0], "date": best[1]}
    ia, ib = re.sub(r"\D", "", str(es.get("counterparty_inn") or "")), re.sub(r"\D", "", str(et.get("counterparty_inn") or ""))
    f["inn"] = 1.0 if ia and ia == ib else 0.0
    na, nb = name_root(es.get("counterparty_name")), name_root(et.get("counterparty_name"))
    f["name"] = 1.0 if na and nb and (na in nb or nb in na) else 0.0
    f["sem"] = float(emb[s] @ emb[t]) if use_sem else 0.0
    # явная ссылка с номером есть, но этому договору не соответствует ни одна — противоречие, только разбор
    f["conflict"] = any(r.get("number") for r in refs) and best[0] == 0.0
    return f

def penalties(s, t, ex):
    es, et = ex[s], ex[t]
    pen = 0.0
    try:
        a, b = es.get("amount"), et.get("amount")
        if a and b and float(a) > float(b) * 1.25: pen += 0.2      # сумма больше цены договора (цель — всегда договор)
    except (TypeError, ValueError): pass
    if es.get("counterparty_inn") and not inn_ok(es.get("counterparty_inn")): pen += 0.05
    return pen

def m_combined(docs, ids, ex, emb, card, use_checks=True, use_sem=True):
    pred, blocked, explain = {}, set(), {}
    for s in ids:
        if status_of(card, s) != "ok": continue
        typ = ALLOWED[card[s]["kind"]]["договор"]
        cand = []
        for t in pool(docs, card, s):
            f = features(s, t, ex, emb, use_sem)
            pen = penalties(s, t, ex) if use_checks else 0.0
            cand.append((t, sum(W[k] * f[k] for k in W) - pen, f))
        if not cand: continue
        cand.sort(key=lambda x: -x[1])
        for t, sc, f in cand[:3]:
            pred[(s, t, typ)] = sc; explain[(s, t)] = f
            if use_checks and f["conflict"]: blocked.add((s, t, typ))
        # тип 8 и неоднозначность: решаем по ссылкам, а не по числу похожих кандидатов
        refs = [r for r in ref_list(ex[s]) if r.get("number")]
        resolved, ambiguous = [], False
        for r in refs:
            hits = [t for t, _, _ in cand if num_sim(r.get("number"), ex[t].get("number")) >= 0.9]
            if len(hits) == 1: resolved.append(hits[0])
            elif len(hits) > 1: ambiguous = True; blocked.update((s, t, typ) for t in hits)   # одна ссылка — два договора
        resolved = list(dict.fromkeys(resolved))
        if len(resolved) >= 2:
            if len(resolved) > 2 or ambiguous:                       # три и более договора — ручной разбор
                blocked.update((s, t, typ) for t, _, _ in cand)
            else:                                                    # две отдельные однозначные ссылки — обе связи
                for t in resolved:
                    if (s, t, typ) not in pred:
                        sc = next(x[1] for x in cand if x[0] == t); pred[(s, t, typ)] = sc
            continue
        # одна цель: рекомендуется только лучший; остальные — альтернативы для разбора
        for t, _, _ in cand[1:3]: blocked.add((s, t, typ))
        if len(cand) > 1 and abs(cand[0][1] - cand[1][1]) < 1e-9: blocked.add((s, cand[0][0], typ))   # равные лидеры
    return pred, blocked

# ---------------- метрики ----------------
def recs(pred, blocked, tau, ids):
    return {k for k, v in pred.items() if v >= tau and k not in blocked and k[0] in ids}

def metrics(rec, gold, inputs):
    """Precision по связям (M=0 → 0, доля ошибок «—»), Recall по эталонным связям,
    охват = документы хотя бы с одной рекомендацией / все входящие документы, точность по документу целиком."""
    M, C, L = len(rec), len(rec & gold), len(gold)
    p = C / M if M else 0.0
    r = C / L if L else 0.0
    rd, gd = defaultdict(set), defaultdict(set)
    for s, t, _ in rec: rd[s].add(t)
    for s, t, _ in gold: gd[s].add(t)
    return {"M": M, "Precision": p, "Доля ошибок": (1 - p) if M else None, "Recall": r,
            "F1": (2 * p * r / (p + r)) if p + r else 0.0,
            "Охват": len({s for s in rd if s in inputs}) / len(inputs) if inputs else 0.0,
            "Документ верно": sum(rd[s] == gd[s] for s in inputs) / len(inputs) if inputs else 0.0}

def tune_tau(pred, blocked, gold, inputs, target_p=0.98):
    """Максимум охвата при Precision ≥ 0,98 и M > 0; при равенстве — выше точность, затем выше порог."""
    best = None
    for tau in np.arange(0.0, 1.01, 0.01):
        m = metrics(recs(pred, blocked, tau, inputs), gold, inputs)
        if m["M"] and m["Precision"] >= target_p:
            key = (m["Охват"], m["Precision"], tau)
            if best is None or key > best[0]: best = (key, round(float(tau), 2))
    return best[1] if best else None   # None = NO_FEASIBLE_CONFIGURATION

def subset(gold, ids): return {g for g in gold if g[0] in ids}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", nargs="*", default=[])
    ap.add_argument("--emb")
    a = ap.parse_args()
    docs, gold = load()
    tune, test = split(docs)
    emb = load_emb(a.emb, docs) if a.emb else tfidf_emb(docs)
    emb_name = "BGE-M3" if a.emb else "TF-IDF (отладка)"
    OUT.mkdir(exist_ok=True)
    rx = {i: regex_extract(docs[i]["text"]) for i in docs}
    systems = {"regex": (rx, {})}
    for p in a.extract:
        systems[Path(p).stem.replace("extract_", "")] = load_extract(p, docs)
    # входящие документы эксперимента: все, кроме договоров (по эталонной разметке вида — только для оценки)
    inp = lambda ids: {i for i in ids if docs[i]["kind"] != "договор"}
    in_tune, in_test = inp(tune), inp(test)
    g_tune, g_test = subset(gold, tune), subset(gold, test)
    hard = {i for i in in_test if docs[i]["hard"]}
    g_hard = subset(gold, hard)
    rows, abl, curves, fields = [], [], {}, []

    def evaluate(label, pb, card):
        pred, blocked = pb
        tau = tune_tau(pred, blocked, g_tune, in_tune)
        n_err = sum(card[i]["error"] for i in in_test)
        if tau is None:
            return {"Метод": label, "τ_a": "недостижимо (NO_FEASIBLE_CONFIGURATION)", "Ошибок обработки": n_err}
        m = metrics(recs(pred, blocked, tau, in_test), g_test, in_test)
        mh = metrics(recs(pred, blocked, tau, hard), g_hard, hard)
        return {"Метод": label, "τ_a": tau, **m, "F1 (трудные)": mh["F1"], "Ошибок обработки": n_err}

    # базовые методы получают ту же карточку (вид, дата), что и комбинированный метод основной модели
    main_sys = next((n for n in systems if n != "regex"), "regex")
    card_main = card_of(systems[main_sys][0], docs)
    rows.append(evaluate(f"Правила (карточка: {main_sys})", m_rules(docs, docs, card_main), card_main))
    rows.append(evaluate(f"Смысловой поиск, {emb_name} (карточка: {main_sys})", m_semantic(docs, docs, emb, card_main), card_main))
    for name, (ex, meta) in systems.items():
        card = card_of(ex, docs)
        label = "Комбинированный, без LLM (регулярки)" if name == "regex" else f"Комбинированный ({name})"
        pb = m_combined(docs, docs, ex, emb, card)
        r = evaluate(label, pb, card); rows.append(r)
        pred, blocked = pb
        curves[label] = [(float(t),) + tuple(metrics(recs(pred, blocked, t, in_test), g_test, in_test)[k] for k in ("Precision", "Охват", "M"))
                         for t in np.arange(0, 1.01, .02)]
        if name != "regex":
            abl.append(dict(r, Метод=f"{name}: полный метод"))
            abl.append(dict(evaluate("", m_combined(docs, docs, ex, emb, card, use_checks=False), card), Метод=f"{name}: без проверок"))
            abl.append(dict(evaluate("", m_combined(docs, docs, ex, emb, card, use_sem=False), card), Метод=f"{name}: без смыслового поиска"))
            card_rx = card_of(rx, docs)
            abl.append(dict(evaluate("", m_combined(docs, docs, rx, emb, card_rx), card_rx), Метод="без LLM-извлечения (регулярки)"))
            ok = [m for m in meta.values() if not m.get("error")]
            secs = [m["seconds"] for m in ok]; toks = [(m.get("usage") or {}).get("total_tokens") for m in ok]
            rows[-1]["с/док"] = round(float(np.mean(secs)), 1) if secs else None
            rows[-1]["токенов/док"] = round(float(np.mean([t for t in toks if t is not None]))) if any(t is not None for t in toks) else None
        acc = defaultdict(list)
        for i in test:
            e, d = ex[i], docs[i]
            acc["вид"].append((e.get("doc_kind") or "") == d["kind"])
            if d["number"]: acc["номер"].append(nnum(e.get("number")) == nnum(d["number"]))
            if d["date"]: acc["дата"].append(pdate(e.get("date")) == d["date"])
            if d["cp_inn"]: acc["ИНН контрагента"].append(re.sub(r"\D", "", str(e.get("counterparty_inn") or "")) == d["cp_inn"])
            if d["amount"]:
                try: acc["сумма"].append(abs(float(e.get("amount") or 0) - d["amount"]) < 1)
                except (TypeError, ValueError): acc["сумма"].append(False)
        fields.append({"Извлечение": name, **{k: round(float(np.mean(v)), 3) for k, v in acc.items()}})

    def md(rs):
        if not rs: return ""
        keys = list(dict.fromkeys(k for r in rs for k in r))
        fmt = lambda v: f"{v:.3f}" if isinstance(v, float) else ("—" if v is None else str(v))
        return "\n".join(["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)] + ["| " + " | ".join(fmt(r.get(k)) for k in keys) + " |" for r in rs])
    rep = [f"# Результаты эксперимента\n\nДокументов: {len(docs)} (настройка {len(tune)}, тест {len(test)}); входящих документов в тесте: {len(in_test)}, "
           f"эталонных связей: {len(g_test)}, трудных входов: {len(hard)}.\nВсе связи — рекомендации, каждую подтверждает бухгалтер. "
           "τ_a подобран на настроечной части: максимум охвата при Precision ≥ 0,98. M — число рекомендованных связей; "
           "при M = 0 Precision = 0, доля ошибок «—». Охват — документы хотя бы с одной рекомендацией / все входящие документы.\n",
           "## Таблица 2.6 — Качество методов (тестовая часть)\n", md(rows), "\n## Таблица 2.7 — Вклад компонентов\n", md(abl),
           "\n## Таблица 2.8 — Точность извлечения реквизитов (доля верных)\n", md(fields)]
    (OUT / "tables.md").write_text("\n".join(rep), encoding="utf-8")
    json.dump(curves, open(OUT / "curves.json", "w", encoding="utf-8"), ensure_ascii=False)
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4.5))
        for label, c in curves.items():
            pts = [x for x in c if x[3]]
            ax.plot([x[2] for x in pts], [1 - x[1] for x in pts], marker=".", label=label)
        ax.set_xlabel("Охват рекомендациями"); ax.set_ylabel("Доля ошибочных рекомендаций (1 − Precision)")
        ax.grid(alpha=.3); ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(OUT / "fig_2_4.png", dpi=200)
    except Exception as e:
        print("график не построен:", e)
    print("\n".join(rep))

if __name__ == "__main__":
    main()
