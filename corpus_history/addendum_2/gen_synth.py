# -*- coding: utf-8 -*-
"""
Генератор синтетической части корпуса (глава 2, п. 2.2).

Создаёт сделки клиентов «Дельта Игрек» по образцу реальных документов:
договор -> дополнительные соглашения, счета, акты, УПД — и вносит трудные случаи
из таблицы 1.1 (типы 1–9). Эталонная разметка известна по построению.

Выход (папка corpus/synth/):
  deal_NNN/doc_XX.txt   тексты документов
  documents.csv         реквизиты каждого документа (эталон извлечения)
  links.csv             эталонные связи (источник, цель, тип)
Запуск: python gen_synth.py [число_сделок] [seed]
"""
import csv, random, sys
from datetime import date, timedelta
from pathlib import Path

N_DEALS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
N_DISTRACT = 8   # договоров-«помех» на клиента
rnd = random.Random(SEED)
OUT = Path(__file__).parent / "corpus" / "synth"

# ---------------- справочники ----------------
def inn10():
    d = [rnd.randint(1, 9)] + [rnd.randint(0, 9) for _ in range(8)]
    k = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    return "".join(map(str, d + [sum(a * b for a, b in zip(d, k)) % 11 % 10]))

def kpp(): return "77" + str(rnd.randint(10, 51)) + "01001"

ROOTS = ["Альфа-Сервис", "Вектор", "Гранит", "Меридиан", "Северный ветер", "Техноком", "Полюс",
         "Импульс", "Орион", "Ладога", "Стройресурс", "Квант", "Светлый город", "Дельфин", "Эталон",
         "Сфера", "Магистраль", "Прогресс", "Атлант", "Профконсалт", "Медиагрупп", "Инфосистемы"]
FORMS = [("ООО", "Общество с ограниченной ответственностью"), ("АО", "Акционерное общество")]
FIRST = ["Иван", "Анна", "Сергей", "Мария", "Олег", "Елена", "Дмитрий", "Ольга"]
LAST = ["Смирнов", "Кузнецов", "Попов", "Волков", "Зайцев", "Морозов", "Лебедев", "Козлов"]

SUBJECTS = {
    "услуги": ["оказание консультационных услуг по вопросам закупочной деятельности",
               "оказание услуг по технической поддержке информационных систем",
               "оказание маркетинговых услуг", "оказание услуг по уборке помещений",
               "оказание юридических услуг по сопровождению хозяйственной деятельности"],
    "поставка": ["поставка компьютерного оборудования", "поставка канцелярских товаров",
                 "поставка строительных материалов", "поставка мебели"],
    "подряд": ["выполнение работ по ремонту помещений", "выполнение пусконаладочных работ",
               "выполнение работ по монтажу локальной вычислительной сети"],
}
GOODS = {"поставка компьютерного оборудования": [("Ноутбук", 85000), ("Монитор 27\"", 24000), ("МФУ", 31000)],
         "поставка канцелярских товаров": [("Бумага А4, пачка", 450), ("Ручка шариковая", 35), ("Папка-регистратор", 210)],
         "поставка строительных материалов": [("Цемент М500, мешок", 520), ("Гипсокартон, лист", 480)],
         "поставка мебели": [("Стол офисный", 14500), ("Кресло офисное", 11800)]}

def person():
    l, f = rnd.choice(LAST), rnd.choice(FIRST)
    if f in ("Анна", "Мария", "Елена", "Ольга"): l += "а"
    return f"{l} {f[0]}.{rnd.choice('АВГДЕИКМНП')}."

def org(root=None):
    short, full = rnd.choice(FORMS)
    root = root or rnd.choice(ROOTS)
    return {"short": f"{short} «{root}»", "full": f"{full} «{root}»", "root": root, "form": short,
            "inn": inn10(), "kpp": kpp(), "head": person()}

# клиенты «Дельта Игрек» (их документы мы и связываем)
CLIENTS = [org(r) for r in ["Ромашка", "Нордтрейд", "Арсенал", "Зенит", "Березка", "Феникс"]]

# ---------------- форматирование ----------------
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
              "сентябрь", "октябрь", "ноябрь", "декабрь"]
def dlong(d): return f"«{d.day:02d}» {MONTHS[d.month-1]} {d.year} г."
def dshort(d): return d.strftime("%d.%m.%Y")
def money(x): return f"{x:,.2f}".replace(",", " ").replace(".", ",")

def num_variant(num):  # тип 2: искажённая запись номера
    v = [num.replace("/", "-"), num.replace("-", "/"), num.split("/")[0] if "/" in num else num + "/" + str(rnd.randint(24, 26)),
         num.replace("0", "О", 1), num.replace("/20", "/")]
    v = [x for x in v if x != num]
    return rnd.choice(v) if v else num + "-1"

def ocr_noise(text, p=0.04):  # тип 3: ошибки распознавания
    swap = {"0": "О", "3": "З", "1": "l", "6": "б", "О": "0", "З": "3", ".": ",", "п": "н", "и": "н"}
    return "".join(swap[c] if c in swap and rnd.random() < p else c for c in text)

def name_variant(o):  # тип 4
    return rnd.choice([f"{o['root']} {o['form']}", f"{o['form']} {o['root']}", f"{o['short']} (филиал в г. Казань)",
                       f"{o['form']} \"{o['root'].upper()}\""])

# ---------------- шаблоны документов ----------------
def t_contract(c):
    s = c["subj"]; kind = c["kind"]
    role = {"услуги": ("Исполнитель", "оказать услуги"), "поставка": ("Поставщик", "поставить товар"),
            "подряд": ("Подрядчик", "выполнить работы")}[kind]
    return f"""ДОГОВОР № {c['num']}
на {s.split(' ', 1)[1] if s.startswith('оказание') else s}
г. Москва {dlong(c['date'])}

{c['client']['full']}, именуемое в дальнейшем «Заказчик», в лице генерального директора {c['client']['head']}, действующего на основании Устава, с одной стороны, и {c['cp']['full']}, именуемое в дальнейшем «{role[0]}», в лице генерального директора {c['cp']['head']}, действующего на основании Устава, с другой стороны, заключили настоящий договор о нижеследующем.

1. ПРЕДМЕТ ДОГОВОРА
1.1. {role[0]} обязуется {role[1]}: {s}, а Заказчик обязуется принять и оплатить {('услуги' if kind=='услуги' else 'товар' if kind=='поставка' else 'работы')} в порядке и на условиях настоящего договора.
2. ЦЕНА И ПОРЯДОК РАСЧЁТОВ
2.1. Цена договора составляет {money(c['amount'])} руб., в т.ч. НДС 20 % — {money(c['amount']/6)} руб.
2.2. Оплата производится на основании счёта {rnd.choice(['в течение 10 рабочих дней', 'ежемесячно', 'поэтапно'])} после подписания акта{' или УПД' if kind=='поставка' else ''}.
3. СРОК ДЕЙСТВИЯ
3.1. Договор действует с {dshort(c['date'])} по {dshort(c['end'])}.
4. РЕКВИЗИТЫ СТОРОН
Заказчик: {c['client']['short']}, ИНН {c['client']['inn']}, КПП {c['client']['kpp']}
{role[0]}: {c['cp']['short']}, ИНН {c['cp']['inn']}, КПП {c['cp']['kpp']}
Р/с {rnd.randint(10**19, 10**20-1)} в ПАО Сбербанк, БИК 044525225

Заказчик ____________ / {c['client']['head']} /      {role[0]} ____________ / {c['cp']['head']} /
"""

def ref_contract(c, err):
    if 1 in err: return rnd.choice(["по договору", "согласно договорённости", "по заявке Заказчика"])
    num = num_variant(c["num"]) if 2 in err else c["num"]
    d = c["date"] + timedelta(days=rnd.choice([3, 5, 7])) if (2 in err and rnd.random() < .5) else c["date"]
    return rnd.choice([f"по договору № {num} от {dshort(d)}", f"Договор {num} от {dlong(d)}",
                       f"к договору № {num}"])

def t_supp(s, c):
    return f"""ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № {s['num']}
{ref_contract(c, s['err']).replace('по договору', 'к договору')}
г. Москва {dlong(s['date'])}

{c['client']['short']} («Заказчик») и {c['cp']['short']} («Исполнитель») договорились:
1. {s['change']}
2. Остальные условия договора остаются неизменными. Соглашение вступает в силу с момента подписания.

Заказчик ____________ / {c['client']['head']} /      Исполнитель ____________ / {c['cp']['head']} /
"""

def seller(c, err):
    o = c["succ"] if 5 in err and c.get("succ") else c["cp"]
    name = name_variant(o) if 4 in err else o["short"]
    inn = "" if 4 in err else f", ИНН {o['inn']}, КПП {o['kpp']}"
    return f"{name}{inn}" + (f" (правопреемник {c['cp']['short']})" if o is not c["cp"] and rnd.random() < .5 else "")

def lines_for(c, amount):
    if c["kind"] == "поставка":
        g = rnd.choice(GOODS[c["subj"]]); q = max(1, round(amount / 1.2 / g[1]))
        return [(g[0], q, "шт", g[1])]
    return [(c["subj"].capitalize() + f" за {MONTHS_NOM[c['_m']-1]} {c['_y']} г.", 1, "усл", round(amount / 1.2, 2))]

def table(rows):
    out = ["№ | Наименование | Кол-во | Ед. | Цена | Сумма"]
    for i, (n, q, u, p) in enumerate(rows, 1): out.append(f"{i} | {n} | {q} | {u} | {money(p)} | {money(p*q)}")
    tot = sum(q * p for _, q, _, p in rows)
    out += [f"Итого: {money(tot)}", f"НДС 20 %: {money(tot*0.2)}", f"Всего к оплате: {money(tot*1.2)}"]
    return "\n".join(out)

def t_invoice(d, cs):
    c = cs[0]
    base = "; ".join(ref_contract(x, d["err"]) for x in cs)
    return f"""СЧЁТ НА ОПЛАТУ № {d['num']} от {dlong(d['date'])}
Поставщик: {seller(c, d['err'])}
Покупатель: {c['client']['short']}, ИНН {c['client']['inn']}
Основание: {base}

{table(d['rows'])}

Руководитель ____________ {c['cp']['head']}   Бухгалтер ____________ {person()}
"""

def t_act(d, cs, upd=False):
    c = cs[0]
    if 7 in d["err"] and d.get("via_supp"):
        base = f"по дополнительному соглашению № {d['via_supp']['num']} от {dshort(d['via_supp']['date'])}"
    else:
        base = "; ".join(ref_contract(x, d["err"]) for x in cs)
    inv = f", счёт № {d['inv']['num']} от {dshort(d['inv']['date'])}" if d.get("inv") and rnd.random() < .6 else ""
    head = (f"УНИВЕРСАЛЬНЫЙ ПЕРЕДАТОЧНЫЙ ДОКУМЕНТ\nСчёт-фактура № {d['num']} от {dshort(d['date'])}   Статус: 1"
            if upd else f"АКТ № {d['num']} от {dlong(d['date'])}\nоб оказании услуг (выполнении работ)")
    tail = ("Основание передачи (сдачи) / получения (приёмки): " + base + inv) if upd else \
           ("Вышеперечисленные услуги выполнены полностью и в срок. Заказчик претензий по объёму, качеству и срокам не имеет.\nОснование: " + base + inv)
    return f"""{head}
{'Продавец' if upd else 'Исполнитель'}: {seller(c, d['err'])}
{'Покупатель' if upd else 'Заказчик'}: {c['client']['short']}, ИНН {c['client']['inn']}

{table(d['rows'])}

{tail}

{'Товар (груз) передал' if upd else 'Исполнитель'} ____________ {c['cp']['head']}     {'Товар (груз) получил' if upd else 'Заказчик'} ____________ {c['client']['head']}
"""

# ---------------- построение сделок ----------------
def new_contract(client, cp, start, kind=None, subj=None, num=None):
    kind = kind or rnd.choice(list(SUBJECTS))
    subj = subj or rnd.choice(SUBJECTS[kind])
    num = num or rnd.choice([f"{rnd.randint(1, 99)}/{start.year % 100}", f"{rnd.randint(100, 999)}-{rnd.choice(['ОК', 'П', 'У', 'К'])}/{start.year}",
                             f"{start.day:02d}{start.month:02d}/{start.year % 100}", str(rnd.randint(1, 60))])
    return {"kind": "contract", "is_contract": True, "ctype": kind, "client": client, "cp": cp, "subj": subj, "num": num, "date": start,
            "end": start + timedelta(days=rnd.choice([180, 365])), "amount": rnd.choice([120, 240, 360, 600, 900, 1500]) * 1000.0,
            "kindc": kind, "err": set()}

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    docs, links = [], []
    cps = {cl["root"]: [org() for _ in range(4)] for cl in CLIENTS}
    for k in range(1, N_DEALS + 1):
        client = rnd.choice(CLIENTS); cp = rnd.choice(cps[client["root"]])
        start = date(2024, 1, 1) + timedelta(days=rnd.randint(0, 800))
        c = new_contract(client, cp, start); c["kind"] = c.pop("kindc")
        deal = [("contract", c, [])]
        hard = rnd.random() < 0.6          # ~60 % сделок с трудными случаями
        # тип 6/9: второй договор с тем же контрагентом (тот же шаблон, другой номер/сумма/год)
        twin = None
        if hard and rnd.random() < 0.5:
            twin = new_contract(client, cp, start + timedelta(days=rnd.choice([-370, -200, 120, 365])),
                                kind=c["kind"], subj=c["subj"] if rnd.random() < .7 else None)
            twin["kind"] = twin.pop("kindc"); deal.append(("contract", twin, []))
            if twin["subj"] == c["subj"]:
                c["err"].add(9); twin["err"].add(9)      # тип 9: похожие шаблонные договоры
        if hard and rnd.random() < 0.5:
            c["succ"] = org(c["cp"]["root"] + "-М")    # тип 5: правопреемник
        supps = []
        for i in range(rnd.choice([0, 0, 1, 1, 2])):
            s = {"kind": "supp", "num": str(i + 1), "date": start + timedelta(days=30 * (i + 1) + rnd.randint(0, 20)),
                 "err": {2} if hard and rnd.random() < .3 else set()}
            if rnd.random() < .5:
                c["amount"] *= 1.2; s["change"] = f"Изложить п. 2.1 в редакции: «Цена договора составляет {money(c['amount'])} руб., в т.ч. НДС 20 %»."
            else:
                c["end"] += timedelta(days=90); s["change"] = f"Продлить срок действия договора до {dshort(c['end'])}."
            supps.append(s); deal.append(("supp", s, [(c, "изменение")]))
        n_per = rnd.randint(1, 3)
        per_amt = round(c["amount"] / max(n_per, 2), -2)
        for j in range(n_per):
            d0 = start + timedelta(days=25 + 30 * j + rnd.randint(0, 5))
            c["_m"], c["_y"] = d0.month, d0.year
            err = set()
            if hard:
                err = set(rnd.sample([1, 2, 3, 4, 5, 6, 7], k=rnd.choice([1, 1, 2])))
                if c.get("succ") and j == n_per - 1: err.add(5)          # правопреемник — в последнем периоде
                if twin and rnd.random() < .6: err.add(6)
                if 5 in err and not c.get("succ"): err.discard(5)
                if 7 in err and not supps: err.discard(7)
                if supps and rnd.random() < .25: err.add(7)
                if 6 in err and not twin: err.discard(6)
            rows = lines_for(c, per_amt)
            targets = [c]
            if twin and (rnd.random() < 0.3 or j == 0) and c["kind"] != "поставка":   # тип 8: один документ по двум договорам
                twin["_m"], twin["_y"] = d0.month, d0.year
                rows += lines_for(twin, round(twin["amount"] / 4, -2)); targets = [c, twin]; err.add(8)
            inv = {"kind": "invoice", "num": str(rnd.randint(1, 900)), "date": d0, "rows": rows, "err": set(err) - {7}}
            deal.append(("invoice", inv, [(t, "основание") for t in targets]))
            upd = c["kind"] == "поставка" and rnd.random() < .8
            act = {"kind": "upd" if upd else "act", "num": str(rnd.randint(1, 900)), "date": d0 + timedelta(days=rnd.randint(3, 25)),
                   "rows": rows, "err": err, "inv": inv, "via_supp": supps[-1] if supps else None}
            deal.append(("upd" if upd else "act", act, [(t, "исполнение") for t in targets] + [(inv, "исполнение")]))
        if twin and 6 in set().union(*[x[1]["err"] for x in deal]):  # тип 6 без номера: убираем номер из ссылки
            for kind, d, _ in deal:
                if 6 in d["err"]: d["err"].add(1)
        # тексты и разметка
        did = f"synth_{k:03d}"; (OUT / did).mkdir(exist_ok=True)
        ids = {}
        deal.sort(key=lambda x: x[1]["date"])
        for i, (kind, d, tg) in enumerate(deal, 1):
            ids[id(d)] = f"{did}/doc_{i:02d}"
        for kind, d, tg in deal:
            cs = [t for t, r in tg if t.get("is_contract")] or [c]
            cc = cs[0]
            txt = {"contract": lambda: t_contract(d), "supp": lambda: t_supp(d, c), "invoice": lambda: t_invoice(d, cs),
                   "act": lambda: t_act(d, cs), "upd": lambda: t_act(d, cs, upd=True)}[kind]()
            if 3 in d["err"]: txt = ocr_noise(txt)
            (OUT / f"{ids[id(d)]}.txt").write_text(txt, encoding="utf-8")
            amt = d["amount"] if kind == "contract" else (sum(q * p for _, q, _, p in d["rows"]) * 1.2 if "rows" in d else "")
            seller_o = cc.get("succ") if 5 in d["err"] and cc.get("succ") else cc["cp"]
            docs.append({"doc_id": ids[id(d)], "deal_id": did, "source": "synth", "client_inn": cc["client"]["inn"],
                         "kind": {"contract": "договор", "supp": "допсоглашение", "invoice": "счёт", "act": "акт", "upd": "УПД"}[kind],
                         "number": d["num"], "date": dshort(d["date"]), "cp_inn": seller_o["inn"], "cp_name": seller_o["short"],
                         "amount": f"{amt:.2f}" if amt != "" else "", "subject": cc["subj"] if kind == "contract" else "",
                         "hard_types": ";".join(map(str, sorted(d["err"])))})
            for t, rel in tg:
                if id(t) in ids: links.append({"source": ids[id(d)], "target": ids[id(t)], "type": rel})
    # договоры-«помехи»: у реального клиента десятки договоров, в т.ч. с теми же контрагентами
    # и по тем же шаблонам (типы 6 и 9), но документы по ним в корпус не попали
    n = 0
    for cl in CLIENTS:
        for _ in range(N_DISTRACT):
            n += 1
            cp = rnd.choice(cps[cl["root"]])
            same = [d for d in docs if d["kind"] == "договор" and d["cp_inn"] == cp["inn"]]
            subj = rnd.choice(same)["subject"] if same and rnd.random() < .6 else None
            kind = next((k for k, v in SUBJECTS.items() if subj in v), None) if subj else None
            c = new_contract(cl, cp, date(2023, 6, 1) + timedelta(days=rnd.randint(0, 900)), kind=kind, subj=subj)
            c["kind"] = c.pop("kindc")
            did = f"synth_x{n:03d}"; (OUT / did).mkdir(exist_ok=True)
            (OUT / did / "doc_01.txt").write_text(t_contract(c), encoding="utf-8")
            docs.append({"doc_id": f"{did}/doc_01", "deal_id": did, "source": "synth", "client_inn": cl["inn"], "kind": "договор",
                         "number": c["num"], "date": dshort(c["date"]), "cp_inn": cp["inn"], "cp_name": cp["short"],
                         "amount": f"{c['amount']:.2f}", "subject": c["subj"], "hard_types": "9" if subj else ""})
    for name, rows in (("documents.csv", docs), ("links.csv", links)):
        with open(OUT / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";"); w.writeheader(); w.writerows(rows)
    hard = sum(1 for d in docs if d["hard_types"])
    print(f"сделок {N_DEALS}, документов {len(docs)}, связей {len(links)}, документов с трудными случаями {hard}")

if __name__ == "__main__":
    main()
