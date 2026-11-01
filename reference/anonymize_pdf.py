# -*- coding: utf-8 -*-
"""
Обезличивание PDF-документов сделок для эксперимента (глава 2).

Вход:  папка, в которой каждая подпапка = одна сделка с PDF-файлами.
Выход: папка out/ с обезличенными текстами (deal_001/doc_01.txt ...),
       manifest.csv (что откуда), review.txt (что проверить глазами),
       corpus_anon.zip (это и нужно прислать).
       Таблица замен сохраняется в НЕ_ОТПРАВЛЯТЬ/mapping.json — её не присылать.

Сохраняются: номера и даты документов, суммы, предмет, структура текста.
Заменяются согласованно (один и тот же объект -> одна и та же замена во всех файлах):
  ИНН (на ИНН с верным контрольным числом), КПП, ОГРН/ОГРНИП, БИК, счета,
  наименования организаций, ФИО, телефоны, e-mail, адреса.

Установка:  pip install pdfplumber
Для сканов: pip install pytesseract  + программа Tesseract с русским языком
            (Windows: установщик UB Mannheim, при установке отметить Russian).
Запуск:     python anonymize_pdf.py "C:\\путь\\к\\папке_со_сделками"
"""
import csv, json, random, re, sys, zipfile
from pathlib import Path

import pdfplumber

rnd = random.Random(2026)
MAP = {"inn": {}, "org": {}, "fio": {}, "num": {}, "phone": {}, "email": {}}


# ---------- извлечение текста ----------
def pdf_text(path):
    pages, ocr_pages = [], 0
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            t = page.extract_text() or ""
            if len(t.strip()) < 30:          # похоже на скан
                t2 = try_ocr(path, i)
                if t2:
                    t, ocr_pages = t2, ocr_pages + 1
            pages.append(t)
    return "\n\f\n".join(pages), len(pages), ocr_pages


def try_ocr(path, page_index):
    """Распознаёт страницу-скан. Нужны: pip install pytesseract и программа Tesseract с русским языком.
    Страница рендерится через pypdfium2 (ставится вместе с pdfplumber), Poppler не нужен."""
    try:
        import os, pytesseract, pypdfium2
        for c in (os.environ.get("TESSERACT_CMD"), r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
            if c and os.path.exists(c):
                pytesseract.pytesseract.tesseract_cmd = c
        pdf = pypdfium2.PdfDocument(str(path))
        img = pdf[page_index].render(scale=300 / 72).to_pil()
        return pytesseract.image_to_string(img, lang="rus")
    except Exception as e:
        if not getattr(try_ocr, "warned", False):
            print("OCR недоступен:", e); try_ocr.warned = True
        return ""


# ---------- генераторы замен ----------
def inn_ok(s):
    d = [int(c) for c in s]
    f = lambda k: sum(a * b for a, b in zip(d, k)) % 11 % 10
    if len(d) == 10:
        return f([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(d) == 12:
        return (f([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10]
                and f([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0]) == d[11])
    return False


def fake_inn(n):
    while True:
        d = [rnd.randint(0, 9) for _ in range(n)]
        d[0] = rnd.randint(1, 9)
        s = "".join(map(str, d))
        f = lambda k: sum(a * b for a, b in zip(d, k)) % 11 % 10
        if n == 10:
            d[9] = f([2, 4, 10, 3, 5, 9, 4, 6, 8])
        else:
            d[10] = f([7, 2, 4, 10, 3, 5, 9, 4, 6, 8])
            d[11] = f([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0])
        s = "".join(map(str, d))
        if s not in MAP["inn"].values():
            return s


def sub_inn(m):
    s = m.group(0)
    if s not in MAP["inn"]:
        MAP["inn"][s] = fake_inn(len(s))
    return MAP["inn"][s]


def sub_num(m):
    s = m.group(0)
    if s not in MAP["num"]:
        MAP["num"][s] = s[:2] + "".join(str(rnd.randint(0, 9)) for _ in range(len(s) - 2))
    return MAP["num"][s]


def label(kind, key, prefix):
    if key not in MAP[kind]:
        MAP[kind][key] = f"{prefix}-{len(MAP[kind]) + 1:02d}"
    return MAP[kind][key]


# ---------- шаблоны ----------
Q = r"[«\"“„']"
QE = r"[»\"”“']"
ORG_FORMS = (r"ООО|АО|ПАО|ЗАО|ОАО|НАО|АНО|НКО|ФГУП|ФГБУ|ФГАУ|ГБУ|ГКУ|ГАУ|МУП|ГУП|МБУ|"
             r"Общество с ограниченной\s+ответственностью|Акционерное\s+общество|"
             r"Публичное\s+акционерное\s+общество|Закрытое\s+акционерное\s+общество|"
             r"Автономная\s+некоммерческая\s+организация|Некоммерческая организация|"
             r"Некоммерческое партн[её]рство|Ассоциация|Фонд|"
             r"(?:Федеральное |Муниципальное )?(?:государственное |муниципальное )?"
             r"(?:унитарное предприятие|бюджетное учреждение|казённое учреждение|казенное учреждение|"
             r"автономное учреждение|учреждение)")
# форма, необязательное уточнение («города Москвы»), название в кавычках, можно на нескольких строках;
# если закрывающей кавычки нет, название заканчивается перед «, именуем…»
RE_ORG = re.compile(rf"(?P<form>\b(?:{ORG_FORMS}))(?P<desc>[^«\"“„\n]{{0,40}}?)\s*{Q}"
                    rf"(?P<name>(?:(?!,\s*именуем)[^»\"”]){{2,160}}?)(?:{QE}|(?=,\s*именуем))", re.I)
PATR = r"(?:(?:ович|евич|ьич)(?:а|у|ем|е)?|(?:овн|евн|ичн|иничн)(?:а|ы|е|у|ой))"
RE_FIO_FULL = re.compile(rf"\b([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+{PATR}\b")
RE_FIO_INIT1 = re.compile(r"\b([А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]+)?)\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.")
RE_FIO_INIT2 = re.compile(r"\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?([А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]+)?)\b")
RE_FIO_UP = re.compile(r"\b([А-ЯЁ]{2,}(?:-[А-ЯЁ]+)?)\s+[А-ЯЁ]{2,}\s+[А-ЯЁ]{2,}(?:ОВИЧ|ЕВИЧ|ЬИЧ|ОВН|ЕВН|ИЧН)[А-ЯЁ]*\b")  # ФИО прописными (чеки)
RE_IP = re.compile(r"\b(ИП|Индивидуальный предприниматель)\s+([А-ЯЁ][а-яё]+)")
RE_EMAIL = re.compile(r"[\w.\-+]+@[\w\-]+\.[\w.\-]+")
RE_PHONE = re.compile(r"(?:\+7|\b8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b")
RE_ACC = re.compile(r"\b\d{20}\b")                       # р/с, к/с
RE_LONG = re.compile(r"\b\d{13}\b|\b\d{15}\b")           # ОГРН, ОГРНИП
RE_KPP = re.compile(r"((?:КПП|К[ИП][ИП]|КПИ|КНП)\s*[:=]?\s*)(\d{9})")  # с учётом ошибок OCR
RE_BIK = re.compile(r"(БИК\s*:?\s*)(\d{9})")
RE_INN_CTX = re.compile(r"(ИНН\s*(?:/\s*КПП)?\s*[:=]?\s*)(\d{12}|\d{10})\b")
RE_INN = re.compile(r"\b\d{10}\b|\b\d{12}\b")
RE_PASS = re.compile(r"(паспорт[^\n]{0,20}?)(\d{2}\s?\d{2}\s?№?\s?\d{6})", re.I)
STREET = (r"(?:ул\.|улица|пр-т|проспект|пр\.|пер\.|переулок|ш\.|шоссе|наб\.|набережная|"
          r"бульвар|б-р|проезд|пл\.|площадь|мкр\.?|микрорайон|тупик)")
HOUSE = r"(?:д\.|дом|корп\.|корпус|стр\.|строение|кв\.|оф\.|офис|пом\.|помещение)"
RE_ADDR = re.compile(
    rf"(?:\b\d{{6}},\s*)?(?:(?:г\.|город|обл\.|область|край|респ\.|республика)\s*[А-ЯЁ][^\n;]{{0,150}}?"
    rf"{STREET}[^\n;]{{0,80}})"
    rf"|\b\d{{6}},\s*[А-ЯЁ][^\n;]{{5,150}}"
    rf"|(?:[А-ЯЁ][а-яё\-]+\s+)?{STREET}\s*[А-ЯЁ0-9][^\n;]{{0,60}}?{HOUSE}\s*[\d/\-]+[^\n;]{{0,60}}"
    rf"|\b[А-ЯЁ][а-яё\-]+,?\s+{HOUSE}\s*\d+[^\n;]{{0,60}}", re.I)
RE_CERT = re.compile(r"^.*\b(?:CN|SN|STREET|G|L|S|ST|O|OU|T|E|SNILS|OGRN|INN|INNLE|OID[\d.]*)\s*=.*$", re.M)
RE_SNILS = re.compile(r"(?:СНИЛС\s*[:=№]?\s*)?\b\d{3}-\d{3}-\d{3}[\s\-]?\d{2}\b|СНИЛС\s*[:=№]?\s*\d{11}\b")
ROLE_WORDS = {"заказчик", "исполнитель", "подрядчик", "стороны", "сторона", "поставщик", "покупатель",
              "продавец", "арендатор", "арендодатель", "интернет"}


STOP = {"директор", "предприниматель", "исполнитель", "заказчик", "генеральный", "главный",
        "бухгалтер", "руководитель", "покупатель", "продавец", "поставщик", "подрядчик",
        "грузополучатель", "грузоотправитель", "арендатор", "арендодатель", "сторона",
        "стороны", "договор", "акт", "счет", "счёт", "подпись", "принял", "сдал", "менеджер",
        "представитель", "доверенность", "уважаемый", "уважаемая", "получатель", "плательщик"}


STOP_PREFIX = ("приложени", "дополнительн", "соглашени", "спецификаци", "председател", "департамент",
               "общество", "москв", "российск", "федерац", "договор", "заказчик", "исполнител")


def stem(surname):
    s = surname.lower()
    return s[:-2] if len(s) > 5 else s


def anonymize(text):
    # 1. Организации: собрать и заменить; потом заменить «голые» упоминания названий
    def org(m):
        name = " ".join(m.group("name").split()).lower()
        if name.startswith("контрагент-") or name in ROLE_WORDS:
            return m.group(0)
        return f"{m.group('form')}{m.group('desc')} «{label('org', name, 'Контрагент')}»"
    text = RE_ORG.sub(org, text)
    for name, lab in sorted(MAP["org"].items(), key=lambda x: -len(x[0])):
        if len(name) >= 4 and not name.startswith("контрагент-"):
            pat = r"\s+".join(re.escape(w) for w in name.split())
            text = re.sub(pat, lab, text, flags=re.I)

    # 1а. Блоки сертификатов электронной подписи (CN=, STREET=, СНИЛС= ...) — строка целиком
    text = RE_CERT.sub("[ДАННЫЕ ЭП]", text)
    text = RE_SNILS.sub("[СНИЛС]", text)

    # 2. Контакты, паспорта, адреса
    text = RE_EMAIL.sub(lambda m: label("email", m.group(0).lower(), "email") + "@example.ru", text)
    text = RE_PHONE.sub(lambda m: label("phone", re.sub(r"\D", "", m.group(0))[-10:], "+7-000-ТЕЛ"), text)
    text = RE_PASS.sub(lambda m: m.group(1) + "[ПАСПОРТ]", text)
    text = RE_ADDR.sub("[АДРЕС]", text)

    # 3. Реквизиты
    text = RE_INN_CTX.sub(lambda m: m.group(1) + sub_inn(re.match(r".*", m.group(2))), text)
    fakes = set(MAP["inn"].values())
    text = RE_INN.sub(lambda m: m.group(0) if m.group(0) in fakes else
                      (sub_inn(m) if inn_ok(m.group(0)) or m.group(0) in MAP["inn"] else m.group(0)), text)
    text = RE_KPP.sub(lambda m: m.group(1) + sub_num(re.match(r".*", m.group(2))), text)
    text = RE_BIK.sub(lambda m: m.group(1) + "04" + sub_num(re.match(r".*", m.group(2)))[2:], text)
    text = RE_ACC.sub(sub_num, text)
    text = RE_LONG.sub(sub_num, text)

    # 4. ФИО (ключ — основа фамилии, чтобы падежи давали одну замену)
    fio = lambda m: m.group(0) if m.group(1).lower() in STOP or m.group(1).lower().startswith(STOP_PREFIX) else label("fio", stem(m.group(1)), "ФИО")
    text = RE_FIO_FULL.sub(fio, text)
    text = RE_FIO_UP.sub(lambda m: label("fio", stem(m.group(1)), "ФИО"), text)
    text = RE_FIO_INIT2.sub(fio, text)   # «П.А. Сидоров» раньше, чем «Сидоров П.А.»
    text = RE_FIO_INIT1.sub(fio, text)
    text = RE_IP.sub(lambda m: f"{m.group(1)} {label('fio', stem(m.group(2)), 'ФИО')}", text)
    # имя и отчество после замены фамилии («ФИО-15 Лидии Александровны»)
    text = re.sub(rf"(ФИО-\d+)\s+[А-ЯЁа-яё\-]+\s+[А-ЯЁ][а-яё]+{PATR}\b", r"\1", text)
    # одиночные упоминания уже известных фамилий
    for st, lab in MAP["fio"].items():
        text = re.sub(rf"\b{re.escape(st.capitalize())}[а-яё]{{0,3}}\b", lab, text)
    # 5. Любые оставшиеся длинные числа (ИКЗ, идентификаторы, лицевые счета, ошибки OCR в метках)
    known = set(MAP["inn"].values()) | set(MAP["num"].values())
    text = re.sub(r"\b\d{9,}\b", lambda m: m.group(0) if m.group(0) in known else sub_num(m), text)
    return text


# ---------- что проверить глазами ----------
RE_SUSPECT = [
    (re.compile(r"\b\d{9,}\b"), "длинное число"),
    (re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:вич|вна|чна)\w*"), "похоже на ФИО"),
    (re.compile(r"\b(?:ул\.|улица|проспект|д\.\s*\d)"), "похоже на адрес"),
    (re.compile(rf"{Q}[А-ЯЁA-Z][^»\"”\n]{{2,40}}{QE}"), "название в кавычках"),
]


def main(src):
    src = Path(src)
    out, secret = Path("out"), Path("НЕ_ОТПРАВЛЯТЬ")
    out.mkdir(exist_ok=True); secret.mkdir(exist_ok=True)
    rows, review = [], []
    deals = sorted([p for p in src.iterdir() if p.is_dir()])
    for di, deal in enumerate(deals, 1):
        did = f"deal_{di:03d}"
        (out / did).mkdir(exist_ok=True)
        for fi, pdf in enumerate(sorted(deal.rglob("*.pdf")), 1):
            doc = f"doc_{fi:02d}"
            try:
                raw, n_pages, n_ocr = pdf_text(pdf)
            except Exception as e:
                print("Ошибка чтения:", pdf, e); continue
            anon = anonymize(raw)
            (out / did / f"{doc}.txt").write_text(anon, encoding="utf-8")
            rows.append([did, doc, n_pages, n_ocr, len(anon.strip()),
                         "нужен OCR" if len(raw.strip()) < 50 else ""])
            known = set(MAP["inn"].values()) | set(MAP["num"].values())
            for ln in anon.splitlines():
                for rx, why in RE_SUSPECT:
                    hits = [h for h in rx.findall(ln) if h not in known and "Контрагент-" not in h]
                    if hits:
                        review.append(f"{did}/{doc}\t{why}\t{ln.strip()[:150]}")
            # исходное имя файла и папки — только в секретную часть
            MAP.setdefault("files", {})[f"{did}/{doc}"] = str(pdf.relative_to(src))
            print("готово:", did, doc)

    with open(out / "manifest.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["сделка", "документ", "страниц", "страниц_OCR", "символов", "замечание"])
        w.writerows(rows)
    (out / "review.txt").write_text("\n".join(review), encoding="utf-8")
    (secret / "mapping.json").write_text(json.dumps(MAP, ensure_ascii=False, indent=1), encoding="utf-8")
    with zipfile.ZipFile("corpus_anon.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in out.rglob("*"):
            z.write(p, p.relative_to(out.parent))
    print(f"\nСделок: {len(deals)}, документов: {len(rows)}.")
    print("Проверьте out/review.txt, затем пришлите corpus_anon.zip. Папку НЕ_ОТПРАВЛЯТЬ не присылайте.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(1)
    main(sys.argv[1])
