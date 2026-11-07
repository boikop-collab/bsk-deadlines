#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BSK Practice OS: калькулятор процесуальних строків.

Покриває ГПК, КАС, ЦПК, КУпАП, КПК. Строки рахує код, не модель.
Тексти норм звірені з Mitralex (актуальні редакції станом на 05.11.2026).

Загальні правила обчислення (закладені в compute):
  * перебіг строку починається з наступного дня після події:
    ч.1 ст.116 ГПК, ч.1 ст.120 КАС, ч.1 ст.123 ЦПК, ч.5 ст.115 КПК;
  * строк місяцями закінчується у відповідне число, а якщо такого немає,
    в останній день місяця: ч.3 ст.116 ГПК, ч.3-4 ст.120 КАС, ч.2 ст.124 ЦПК,
    ч.4 ст.115 КПК;
  * якщо останній день неробочий, останнім днем є перший наступний робочий:
    ч.4 ст.116 ГПК, ч.6 ст.120 КАС, ч.3 ст.124 ЦПК, ч.7 ст.115 КПК;
  * для КУпАП спеціальних правил обчислення немає, застосовується той самий
    підхід (наступний день, перенесення з неробочого дня) з позначкою.

Неробочі дні: субота і неділя. Святкові дні в період воєнного стану
не є вихідними, тому список HOLIDAYS за замовчуванням порожній; його можна
доповнити у holidays.txt (по одній даті YYYY-MM-DD у рядку) поруч зі скриптом.

Використання:
  python deadlines.py rules
  python deadlines.py compute gpk_apel_rishennia 2026-10-20
  python deadlines.py compute gpk_vidzyv_sproshch 2026-11-02 --served 2026-11-03
  python deadlines.py compute gpk_vidzyv_zahal 2026-11-02 --court-days 20
  python deadlines.py batch input.json [--today 2026-11-05] [--csv out.csv]
  python deadlines.py selftest

Формат input.json для batch: список об'єктів
  {"case": "000/0000/26", "client": "Клієнт", "rule": "gpk_apel_rishennia",
   "date": "2026-10-20", "served": null, "court_days": null,
   "court_end": null, "note": "..."}
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import Optional

# ---------------------------------------------------------------- довідник норм
# unit: "d" дні, "m" місяці, "court" строк встановлює суд (потрібно court_days
# або court_end), "info" інформаційний маркер без обчислення.
# start: "event" від дати акта/події, "served" від дати вручення.

RULES: dict[str, dict] = {
    # ===================== ГПК (1798-12) =====================
    "gpk_vidzyv_sproshch": dict(code="ГПК", title="Відзив (спрощене провадження)",
        amount=15, unit="d", start="served", norm="ч.1 ст.251 ГПК",
        note="15 днів з дня вручення ухвали про відкриття провадження."),
    "gpk_klopot_vyklyk": dict(code="ГПК", title="Клопотання про розгляд з повідомленням сторін (спрощене)",
        amount=15, unit="d", start="served", norm="ч.7 ст.252 ГПК",
        note="Відповідач подає в строк для подання відзиву."),
    "gpk_vidzyv_zahal": dict(code="ГПК", title="Відзив (загальне провадження)",
        amount=None, unit="court", start="served", norm="ч.8 ст.165 ГПК",
        note="Строк встановлює суд, не менше 15 днів з дня вручення ухвали про відкриття."),
    "gpk_zaperech_sproshch": dict(code="ГПК", title="Заперечення проти розгляду у спрощеному провадженні",
        amount=None, unit="court", start="served", norm="ч.2, ч.4 ст.250 ГПК",
        note="Лише якщо суд визначив такий строк в ухвалі про відкриття."),
    "gpk_zaperechennia": dict(code="ГПК", title="Заперечення на відповідь на відзив",
        amount=None, unit="court", start="served", norm="ч.2 ст.251 ГПК",
        note="Строк встановлює суд в ухвалі про відкриття провадження."),
    "gpk_usunennia_nedolikiv": dict(code="ГПК", title="Усунення недоліків",
        amount=None, unit="court", start="served", norm="ст.174 ГПК",
        note="Строк зазначено в ухвалі про залишення без руху."),
    "gpk_apel_rishennia": dict(code="ГПК", title="Апеляційна скарга на рішення",
        amount=20, unit="d", start="event", norm="ч.1 ст.256 ГПК",
        note="Від проголошення; якщо скорочене рішення або розгляд без повідомлення, "
             "від дня складення повного рішення. Поновлення: 20 днів з вручення (ч.2 ст.256).",
        entry_into_force="ч.1 ст.241 ГПК"),
    "gpk_apel_ukhvala": dict(code="ГПК", title="Апеляційна скарга на ухвалу",
        amount=10, unit="d", start="event", norm="ч.1 ст.256 ГПК",
        note="Від проголошення або складення повного тексту (розгляд без повідомлення)."),
    "gpk_kasatsiia": dict(code="ГПК", title="Касаційна скарга",
        amount=20, unit="d", start="event", norm="ч.1 ст.288 ГПК",
        note="Від проголошення або складення повного тексту."),
    "gpk_skasuv_nakazu": dict(code="ГПК", title="Заява про скасування судового наказу",
        amount=15, unit="d", start="served", norm="ч.1 ст.157 ГПК",
        note="15 днів з дня вручення копії судового наказу."),
    "gpk_rozstrochka_info": dict(code="ГПК", title="Розстрочка/відстрочка виконання (межа)",
        amount=12, unit="m", start="event", norm="ч.5 ст.331 ГПК",
        note="Розстрочка не може перевищувати 1 року з дня ухвалення рішення; "
             "заява розглядається 20 днів (ч.2 ст.331)."),

    # ===================== КАС (2747-15) =====================
    "kas_vidzyv_sproshch": dict(code="КАС", title="Відзив (спрощене провадження)",
        amount=15, unit="d", start="served", norm="ч.1 ст.261 КАС",
        note="15 днів з дня вручення відповідачу ухвали про відкриття."),
    "kas_vidzyv_zahal": dict(code="КАС", title="Відзив (загальне провадження)",
        amount=None, unit="court", start="served", norm="ч.5 ст.162 КАС",
        note="Строк встановлює суд, не менше 15 днів з дня вручення ухвали."),
    "kas_apel_rishennia": dict(code="КАС", title="Апеляційна скарга на рішення",
        amount=30, unit="d", start="event", norm="ч.1 ст.295 КАС",
        note="Від проголошення; при письмовому провадженні або скороченому рішенні "
             "від складення повного тексту.", entry_into_force="ч.1 ст.255 КАС"),
    "kas_apel_ukhvala": dict(code="КАС", title="Апеляційна скарга на ухвалу",
        amount=15, unit="d", start="event", norm="ч.1 ст.295 КАС", note=""),
    "kas_kasatsiia": dict(code="КАС", title="Касаційна скарга",
        amount=30, unit="d", start="event", norm="ч.1 ст.329 КАС", note=""),
    "kas_286_pozov": dict(code="КАС", title="Позов про оскарження постанови про адмінправопорушення",
        amount=10, unit="d", start="event", norm="ч.2 ст.286 КАС",
        note="10 днів з дня ухвалення; у сфері безпеки дорожнього руху (ДТП, автофіксація) "
             "з дня ВРУЧЕННЯ: тоді передавайте дату вручення як date."),
    "kas_286_apel": dict(code="КАС", title="Апеляція у справі за ст.286 КАС",
        amount=10, unit="d", start="event", norm="ч.4 ст.286 КАС", note=""),

    # ===================== ЦПК (1618-15) =====================
    "cpk_vidzyv": dict(code="ЦПК", title="Відзив",
        amount=None, unit="court", start="served", norm="ч.7 ст.178 ЦПК",
        note="Строк встановлює суд, не менше 15 днів з дня вручення ухвали."),
    "cpk_apel_rishennia": dict(code="ЦПК", title="Апеляційна скарга на рішення",
        amount=30, unit="d", start="event", norm="ч.1 ст.354 ЦПК",
        note="Від проголошення або складення повного тексту.", entry_into_force="ч.1 ст.273 ЦПК"),
    "cpk_apel_ukhvala": dict(code="ЦПК", title="Апеляційна скарга на ухвалу",
        amount=15, unit="d", start="event", norm="ч.1 ст.354 ЦПК", note=""),
    "cpk_kasatsiia": dict(code="ЦПК", title="Касаційна скарга",
        amount=30, unit="d", start="event", norm="ч.1 ст.390 ЦПК", note=""),
    "cpk_skasuv_nakazu": dict(code="ЦПК", title="Заява про скасування судового наказу",
        amount=15, unit="d", start="served", norm="ч.1 ст.170 ЦПК", note=""),

    # ===================== КУпАП (8073-X) =====================
    "kupap_skarha_postanova": dict(code="КУпАП", title="Скарга на постанову у справі про АП",
        amount=10, unit="d", start="event", norm="ст.289 КУпАП",
        note="10 днів з дня винесення; у сфері безпеки дорожнього руху з дня ВРУЧЕННЯ постанови."),
    "kupap_apel_sud": dict(code="КУпАП", title="Апеляція на постанову судді у справі про АП",
        amount=10, unit="d", start="event", norm="ч.2 ст.294 КУпАП",
        note="10 днів з дня винесення постанови. Постанова набирає сили після спливу строку (ч.1 ст.294)."),
    "kupap_shtraf": dict(code="КУпАП", title="Сплата штрафу",
        amount=15, unit="d", start="served", norm="ч.1 ст.307 КУпАП",
        note="Не пізніше 15 днів з дня вручення постанови; при оскарженні 15 днів з повідомлення "
             "про залишення скарги без задоволення."),
    "kupap_strok_sud": dict(code="КУпАП", title="Граничний строк накладення стягнення судом",
        amount=3, unit="m", start="event", norm="ч.2 ст.38 КУпАП",
        note="3 місяці з дня вчинення (дата ДТП). Для захисту: після спливу стягнення не накладається.",
        no_roll=True),
    "kupap_strok_organ": dict(code="КУпАП", title="Граничний строк накладення стягнення органом",
        amount=2, unit="m", start="event", norm="ч.1 ст.38 КУпАП",
        note="2 місяці з дня вчинення (поліція тощо).", no_roll=True),

    # ===================== КПК (4651-17) =====================
    "kpk_apel_vyrok": dict(code="КПК", title="Апеляція на вирок",
        amount=30, unit="d", start="event", norm="п.1 ч.2 ст.395 КПК",
        note="Для особи під вартою або ухвалено без виклику: з дня отримання копії (ч.3 ст.395)."),
    "kpk_apel_ukhvala_sudu": dict(code="КПК", title="Апеляція на іншу ухвалу суду 1 інстанції",
        amount=7, unit="d", start="event", norm="п.2 ч.2 ст.395 КПК", note=""),
    "kpk_apel_slidchyi_suddia": dict(code="КПК", title="Апеляція на ухвалу слідчого судді",
        amount=5, unit="d", start="event", norm="п.3 ч.2 ст.395 КПК",
        note="Якщо постановлено без виклику особи: з дня отримання копії (ч.3 ст.395)."),
    "kpk_apel_vartu": dict(code="КПК", title="Апеляція на ухвалу про тримання під вартою (судове провадження)",
        amount=5, unit="d", start="event", norm="п.1-1 ч.2 ст.395 КПК", note=""),
    "kpk_skarha_304": dict(code="КПК", title="Скарга слідчому судді на рішення/дії/бездіяльність",
        amount=10, unit="d", start="event", norm="ч.1 ст.304 КПК",
        note="Якщо рішення оформлено постановою: з дня отримання копії."),
    "kpk_kasatsiia": dict(code="КПК", title="Касаційна скарга",
        amount=3, unit="m", start="event", norm="ч.2 ст.426 КПК",
        note="3 місяці з дня проголошення рішення апеляційним судом."),

    # ===================== Виконання =====================
    "vp_zbir_info": dict(code="ВП", title="Ризик виконавчого збору 10%",
        amount=None, unit="info", start="event", norm="ч.2, ч.9 ст.27 Закону № 1404-VIII",
        note="Збір 10% стягується з відкриттям ДВС-провадження; не стягується, якщо рішення "
             "виконано до винесення постанови про відкриття (ч.9 ст.27). Платити до відкриття ВП."),
}

CODE_FOR_PREFIX = {"gpk": "ГПК", "kas": "КАС", "cpk": "ЦПК", "kupap": "КУпАП", "kpk": "КПК", "vp": "ВП"}

# ---------------------------------------------------------------- календар

def _load_holidays() -> set[date]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "holidays.txt")
    out: set[date] = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.add(date.fromisoformat(line))
    return out


HOLIDAYS = _load_holidays()


def is_working(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def roll_forward(d: date) -> date:
    while not is_working(d):
        d += timedelta(days=1)
    return d


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Невідомий формат дати: {s}")


# ---------------------------------------------------------------- обчислення

@dataclass
class Result:
    rule: str
    code: str
    title: str
    norm: str
    start_from: str
    raw_end: Optional[str]
    deadline: Optional[str]
    rolled: bool
    entry_into_force: Optional[str]
    days_left: Optional[int]
    flags: list
    note: str
    case: Optional[str] = None
    client: Optional[str] = None
    user_note: Optional[str] = None


def compute(rule: str, event: date, served: Optional[date] = None,
            court_days: Optional[int] = None, court_end: Optional[date] = None,
            today: Optional[date] = None) -> Result:
    if rule not in RULES:
        raise KeyError(f"Невідоме правило: {rule}. Див. 'python deadlines.py rules'")
    r = RULES[rule]
    flags: list[str] = []
    today = today or date.today()

    base = event
    if r["start"] == "served":
        if served:
            base = served
        else:
            flags.append("ВРУЧЕННЯ НЕ ПІДТВЕРДЖЕНО: рахую від дати акта, перевірте дату доставки в Е-суді")

    raw_end: Optional[date] = None
    if r["unit"] == "d":
        raw_end = base + timedelta(days=r["amount"])
    elif r["unit"] == "m":
        raw_end = add_months(base, r["amount"])
    elif r["unit"] == "court":
        if court_end:
            raw_end = court_end
        elif court_days:
            raw_end = base + timedelta(days=int(court_days))
        else:
            flags.append("СТРОК ВСТАНОВЛЮЄ СУД: внесіть court_days або court_end з ухвали")
            if rule in ("gpk_vidzyv_zahal", "kas_vidzyv_zahal", "cpk_vidzyv"):
                raw_end = base + timedelta(days=15)
                flags.append("Показано мінімальні 15 днів як орієнтир")
    elif r["unit"] == "info":
        flags.append("Інформаційний маркер")

    # дата, прямо вказана в ухвалі (або внесена вручну), має пріоритет
    if court_end and r["unit"] != "court":
        if raw_end and court_end != raw_end:
            flags.append(f"Перевизначено датою з ухвали/вручну (розрахунок рушія: {roll_forward(raw_end).isoformat()})")
        raw_end = court_end

    deadline = raw_end
    rolled = False
    if raw_end and not r.get("no_roll"):
        deadline = roll_forward(raw_end)
        rolled = deadline != raw_end
        if r["code"] == "КУпАП":
            flags.append("КУпАП не має окремої норми про перенесення; застосовано загальний підхід")

    eif = None
    if deadline and r.get("entry_into_force"):
        eif = (deadline + timedelta(days=1)).isoformat()

    days_left = (deadline - today).days if deadline else None
    if days_left is not None:
        if days_left < 0:
            flags.append("ПРОСТРОЧЕНО")
        elif days_left <= 3:
            flags.append("ТЕРМІНОВО (<=3 дні)")
        elif days_left <= 7:
            flags.append("Цього тижня (<=7 днів)")

    return Result(rule=rule, code=r["code"], title=r["title"], norm=r["norm"],
                  start_from=base.isoformat(), raw_end=raw_end.isoformat() if raw_end else None,
                  deadline=deadline.isoformat() if deadline else None, rolled=rolled,
                  entry_into_force=eif, days_left=days_left, flags=flags, note=r["note"])


def run_batch(items: list[dict], today: Optional[date]) -> list[Result]:
    out = []
    for it in items:
        try:
            res = compute(it["rule"], parse_date(it["date"]), parse_date(it.get("served")),
                          it.get("court_days"), parse_date(it.get("court_end")), today)
        except Exception as exc:  # noqa: BLE001
            res = Result(rule=it.get("rule", "?"), code="?", title="ПОМИЛКА", norm="", start_from="",
                         raw_end=None, deadline=None, rolled=False, entry_into_force=None,
                         days_left=None, flags=[f"ПОМИЛКА: {exc}"], note="")
        res.case = it.get("case")
        res.client = it.get("client")
        res.user_note = it.get("note")
        out.append(res)
    out.sort(key=lambda x: (x.deadline is None, x.deadline or ""))
    return out


# ---------------------------------------------------------------- самоперевірка

def selftest() -> None:
    t = date(2026, 11, 5)
    # рішення без виклику, повний текст 20.10.2026 -> 09.11.2026 (пн)
    assert compute("gpk_apel_rishennia", date(2026, 10, 20), today=t).deadline == "2026-11-09"
    # ухвала, повний текст 30.10.2026 -> 09.11.2026 (пн)
    assert compute("gpk_apel_ukhvala", date(2026, 10, 30), today=t).deadline == "2026-11-09"
    # повне рішення 29.10.2026 -> 18.11.2026, чинність 19.11.2026
    r = compute("gpk_apel_rishennia", date(2026, 10, 29), today=t)
    assert r.deadline == "2026-11-18" and r.entry_into_force == "2026-11-19"
    # перенесення з суботи: 04.11 + 10 = 14.11 (сб) -> 16.11 (пн)
    r = compute("gpk_apel_ukhvala", date(2026, 11, 4), today=t)
    assert r.raw_end == "2026-11-14" and r.deadline == "2026-11-16" and r.rolled
    # місяці з кінцем місяця: 30.11 + 3 міс = 28.02.2027 (нд) -> 01.03.2027
    assert compute("kpk_kasatsiia", date(2026, 11, 30), today=t).deadline == "2027-03-01"
    # КУпАП ст.38 без перенесення
    assert compute("kupap_strok_sud", date(2026, 8, 31), today=t).deadline == "2026-11-30"
    # відзив у спрощеному від вручення 03.11 -> 18.11
    assert compute("gpk_vidzyv_sproshch", date(2026, 11, 2), served=date(2026, 11, 3), today=t).deadline == "2026-11-18"
    print("selftest OK")


# ---------------------------------------------------------------- CLI

def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="BSK Practice OS: процесуальні строки")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules")
    sub.add_parser("selftest")
    c = sub.add_parser("compute")
    c.add_argument("rule")
    c.add_argument("date")
    c.add_argument("--served")
    c.add_argument("--court-days", type=int)
    c.add_argument("--court-end")
    c.add_argument("--today")
    b = sub.add_parser("batch")
    b.add_argument("input")
    b.add_argument("--today")
    b.add_argument("--csv")
    a = p.parse_args(argv)

    if a.cmd == "rules":
        for k, r in RULES.items():
            amt = f"{r['amount']} {r['unit']}" if r["amount"] else r["unit"]
            print(f"{k:28s} {r['code']:6s} {amt:8s} {r['norm']:30s} {r['title']}")
        return
    if a.cmd == "selftest":
        selftest()
        return
    if a.cmd == "compute":
        res = compute(a.rule, parse_date(a.date), parse_date(a.served), a.court_days,
                      parse_date(a.court_end), parse_date(a.today))
        print(json.dumps(asdict(res), ensure_ascii=False, indent=2))
        return
    if a.cmd == "batch":
        with open(a.input, encoding="utf-8") as fh:
            items = json.load(fh)
        res = run_batch(items, parse_date(a.today))
        rows = [asdict(r) for r in res]
        if a.csv:
            with open(a.csv, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["rule"])
                w.writeheader()
                for row in rows:
                    row = dict(row)
                    row["flags"] = "; ".join(row["flags"])
                    w.writerow(row)
        print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
