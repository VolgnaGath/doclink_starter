"""Application port of the reference algorithm; dictionaries are client-scoped in PostgreSQL.

No dependency on reference/, no file names or evaluation metadata enter preparation.
The client row serializes dictionary allocation and prevents concurrent collisions.
"""

import re
import secrets
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnonIdentity, AnonMap, Client, Document, DocumentText

ORG_FORMS = (
    r"ООО|ПАО|ЗАО|ОАО|НАО|АО|АНО|НКО|ФГУП|ФГБУ|ФГАУ|ГБУ|ГКУ|ГАУ|МУП|ГУП|МБУ|"
    r"Общество с ограниченной\s+ответственностью|Акционерное\s+общество|"
    r"Публичное\s+акционерное\s+общество|Закрытое\s+акционерное\s+общество|"
    r"Автономная\s+некоммерческая\s+организация|Некоммерческая организация|"
    r"Некоммерческое партн[её]рство|Ассоциация|Фонд|"
    r"(?:Федеральное |Муниципальное )?(?:государственное |муниципальное )?"
    r"(?:унитарное предприятие|бюджетное учреждение|каз[её]нное учреждение|"
    r"автономное учреждение|учреждение)"
)
ORG = re.compile(
    rf"(?P<form>\b(?:{ORG_FORMS}))(?P<desc>[^«\"“„\n]{{0,40}}?)\s*[«\"“„']"
    r"(?P<name>(?:(?!,\s*именуем)[^»\"”]){2,160}?)(?:[»\"”“']|(?=,\s*именуем))",
    re.I,
)
PATR = r"(?:(?:ович|евич|ьич)(?:а|у|ем|е)?|(?:овн|евн|ичн|иничн)(?:а|ы|е|у|ой))"
FIO = [
    re.compile(rf"\b([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+{PATR}\b"),
    re.compile(
        r"\b([А-ЯЁ]{2,}(?:-[А-ЯЁ]+)?)\s+[А-ЯЁ]{2,}\s+[А-ЯЁ]{2,}(?:ОВИЧ|ЕВИЧ|ЬИЧ|ОВН|ЕВН|ИЧН)[А-ЯЁ]*\b"
    ),
    re.compile(r"\b[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?([А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]+)?)\b"),
    re.compile(r"\b([А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁ][а-яё]+)?)\s+[А-ЯЁ]\.\s?[А-ЯЁ]\."),
]
STOP = set(
    (
        "директор предприниматель исполнитель заказчик генеральный главный бухгалтер "
        "руководитель покупатель продавец поставщик подрядчик грузополучатель грузоотправитель "
        "арендатор арендодатель сторона стороны договор акт счет счёт подпись принял сдал "
        "менеджер представитель доверенность уважаемый уважаемая получатель плательщик"
    ).split()
)
STOP_PREFIX = (
    "приложени",
    "дополнительн",
    "соглашени",
    "спецификаци",
    "председател",
    "департамент",
    "общество",
    "москв",
    "российск",
    "федерац",
    "договор",
    "заказчик",
    "исполнител",
)
STREET = (
    r"(?:ул\.|улица|пр-т|проспект|пр\.|пер\.|переулок|ш\.|шоссе|наб\.|набережная|"
    r"бульвар|б-р|проезд|пл\.|площадь|мкр\.?|микрорайон|тупик)"
)
HOUSE = r"(?:д\.|дом|корп\.|корпус|стр\.|строение|кв\.|оф\.|офис|пом\.|помещение)"
ADDRESS = re.compile(
    rf"(?:\b\d{{6}},\s*)?(?:(?:г\.|город|обл\.|область|край|респ\.|республика)\s*[А-ЯЁ][^\n;]{{0,150}}?"
    rf"{STREET}[^\n;]{{0,80}})|\b\d{{6}},\s*[А-ЯЁ][^\n;]{{5,150}}"
    rf"|(?:[А-ЯЁ][а-яё\-]+\s+)?{STREET}\s*[А-ЯЁ0-9][^\n;]{{0,60}}?{HOUSE}\s*[\d/\-]+[^\n;]{{0,60}}"
    rf"|\b[А-ЯЁ][а-яё\-]+,?\s+{HOUSE}\s*\d+[^\n;]{{0,60}}",
    re.I,
)


def fake_inn(length: int) -> str:
    digits = [secrets.randbelow(10) for _ in range(length)]
    digits[0] = secrets.randbelow(9) + 1

    def check(weights: list[int]) -> int:
        return sum(a * b for a, b in zip(digits, weights, strict=False)) % 11 % 10

    if length == 10:
        digits[9] = check([2, 4, 10, 3, 5, 9, 4, 6, 8])
    else:
        digits[10] = check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8])
        digits[11] = check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0])
    return "".join(map(str, digits))


def inn_ok(value: str) -> bool:
    digits = list(map(int, value))

    def check(weights: list[int]) -> int:
        return sum(a * b for a, b in zip(digits, weights, strict=False)) % 11 % 10

    if len(value) == 10:
        return digits[9] == check([2, 4, 10, 3, 5, 9, 4, 6, 8])
    return (
        len(value) == 12
        and digits[10] == check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8])
        and digits[11] == check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8, 0])
    )


def transform(text: str, replace: Callable[[str, str], str]) -> str:
    protected: list[str] = []

    def protect(value: str) -> str:
        protected.append(value)
        return f"ЗАЩИЩЕНОФРАГМЕНТ{len(protected) - 1}END"

    text = re.sub(
        r"((?:договор\w*|сч[её]т\w*|акт\w*|соглашени\w*|УПД)[^\n№]{0,80}№\s*)([^\s,;]+)",
        lambda m: m[1] + protect(m[2]),
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b\d[\d \u00a0]*(?:[,.]\d{1,2})?\s*(?:руб(?:лей|ля|ль|\.)?|₽)",
        lambda m: protect(m[0]),
        text,
        flags=re.I,
    )

    def org(m: re.Match[str]) -> str:
        name = m["name"]
        if name.lower().startswith("контрагент-") or name.lower() in STOP:
            return m[0]
        return f"{m['form']}{m['desc']} «{replace('org', name)}»"

    text = ORG.sub(org, text)
    patterns = [
        (
            "certificate",
            r"^.*\b(?:CN|SN|STREET|G|L|S|ST|O|OU|T|E|SNILS|OGRN|INN|INNLE|OID[\d.]*)\s*=.*$",
        ),
        (
            "snils",
            r"(?:СНИЛС\s*[:=№]?\s*)?\b\d{3}-\d{3}-\d{3}[\s\-]?\d{2}\b|СНИЛС\s*[:=№]?\s*\d{11}\b",
        ),
        ("email", r"[\w.\-+]+@[\w\-]+\.[\w.\-]+"),
        ("phone", r"(?:\+7|\b8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b"),
        ("passport", r"паспорт[^\n]{0,20}?\d{2}\s?\d{2}\s?№?\s?\d{6}"),
    ]
    for kind, pattern in patterns:
        text = re.sub(pattern, lambda m, k=kind: replace(k, m[0]), text, flags=re.I | re.M)
    text = ADDRESS.sub(lambda m: replace("address", m[0]), text)
    # Contextual and checksum-valid INNs, then other identifiers (including OCR labels).
    text = re.sub(
        r"(ИНН\s*(?:/\s*КПП)?\s*[:=]?\s*)(\d{12}|\d{10})\b",
        lambda m: m[1] + replace("inn", m[2]),
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b(?:\d{10}|\d{12})\b", lambda m: replace("inn", m[0]) if inn_ok(m[0]) else m[0], text
    )
    text = re.sub(
        r"((?:КПП|К[ИП][ИП]|КПИ|КНП|БИК)\s*[:=]?\s*)(\d{9})",
        lambda m: m[1] + replace("num", m[2]),
        text,
        flags=re.I,
    )
    for pattern in FIO:

        def fio(m: re.Match[str]) -> str:
            if m[1].lower() in STOP or m[1].lower().startswith(STOP_PREFIX):
                return m[0]
            return replace("fio", m[0])

        text = pattern.sub(fio, text)
    text = re.sub(
        r"\b(ИП|Индивидуальный предприниматель)\s+([А-ЯЁ][а-яё]+)",
        lambda m: m[1] + " " + replace("fio", m[2]),
        text,
    )
    text = re.sub(r"\b\d{9,}\b", lambda m: replace("identifier", m[0]), text)
    for index, value in enumerate(protected):
        text = text.replace(f"ЗАЩИЩЕНОФРАГМЕНТ{index}END", value)
    return text


def prepare(session: Session, document: Document) -> tuple[str, str, list[tuple[str, str]]]:
    client = session.scalar(select(Client).where(Client.id == document.client_id).with_for_update())
    stored = session.get(DocumentText, document.id)
    if client is None or stored is None:
        raise ValueError("Нет клиента или текста")
    if document.is_anonymized:
        return stored.text, f"{client.name}; ИНН {client.inn or 'неизвестен'}", []
    identities = session.scalars(
        select(AnonIdentity).where(AnonIdentity.client_id == client.id)
    ).all()
    dictionary = {(row.kind, row.original): row.replacement for row in identities}
    used = {row.replacement for row in identities}
    # Avoid replacement colliding with any real identifier in this document/context.
    source = stored.text + "\n" + client.name + "\n" + (client.inn or "")
    if any(row.replacement in source for row in identities):
        raise ValueError("Исходный текст совпал с ранее выделенной заменой; требуется разбор")
    mappings: dict[str, str] = {}
    existing = {
        (r.kind, r.original)
        for r in session.scalars(select(AnonMap).where(AnonMap.document_id == document.id))
    }

    def replace(kind: str, original: str) -> str:
        if original in used:
            return original
        key = " ".join(original.split()).lower() if kind in {"org", "fio", "email"} else original
        if kind == "fio":
            surname = re.search(r"[А-ЯЁа-яё]{3,}", original)
            if surname:
                key = surname[0].lower()
                key = key[:-2] if len(key) > 5 else key
        identity = (kind, key)
        if identity not in dictionary:
            candidate_index = len(dictionary) + 1
            while True:
                if kind == "inn":
                    value = fake_inn(len(original))
                elif kind in {"num", "identifier"}:
                    value = original[:2] + "".join(
                        str(secrets.randbelow(10)) for _ in range(len(original) - 2)
                    )
                else:
                    prefix = {"org": "Контрагент", "fio": "ФИО"}.get(kind, kind.upper())
                    value = f"{prefix}-{candidate_index:04d}"
                if value not in used and value not in source:
                    break
                candidate_index += 1
            dictionary[identity] = value
            used.add(value)
            session.add(
                AnonIdentity(client_id=client.id, kind=kind, original=key, replacement=value)
            )
        value = dictionary[identity]
        mappings[original] = value
        if (kind, original) not in existing:
            session.add(
                AnonMap(document_id=document.id, kind=kind, original=original, replacement=value)
            )
            existing.add((kind, original))
        return value

    # Explicit client identity also covers unquoted organization names.
    replace("org", client.name)
    if client.inn:
        replace("inn", client.inn)
    transformed = transform(stored.text, replace)
    context = transform(f"{client.name}; ИНН {client.inn or 'неизвестен'}", replace)
    for (kind, original), _replacement in sorted(
        dictionary.items(), key=lambda item: -len(item[0][1])
    ):
        if kind == "org" and len(original) >= 4:
            pattern = r"\s+".join(re.escape(w) for w in original.split())
            transformed = re.sub(pattern, lambda m: replace("org", m[0]), transformed, flags=re.I)
            context = re.sub(pattern, lambda m: replace("org", m[0]), context, flags=re.I)
        elif kind == "fio":
            transformed = re.sub(
                rf"\b{re.escape(original)}[а-яё]{{0,3}}\b",
                lambda m: replace("fio", m[0]),
                transformed,
                flags=re.I,
            )
    stored.anonymized_text = transformed
    session.flush()
    reverse = [
        (row.replacement, row.original)
        for row in session.scalars(
            select(AnonMap).where(AnonMap.document_id == document.id).order_by(AnonMap.id)
        )
    ]
    return transformed, context, reverse


def restore(value: object, reverse: list[tuple[str, str]]) -> object:
    if isinstance(value, str):
        for replacement, original in sorted(reverse, key=lambda pair: -len(pair[0])):
            value = value.replace(replacement, original)
        return value
    if isinstance(value, list):
        return [restore(item, reverse) for item in value]
    if isinstance(value, dict):
        return {key: restore(item, reverse) for key, item in value.items()}
    return value
