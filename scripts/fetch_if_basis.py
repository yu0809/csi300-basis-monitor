#!/usr/bin/env python3
"""Fetch CSI 300 spot and IF futures basis from public quote endpoints."""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
from calendar import Calendar
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SINA_QUOTE_URL = "https://hq.sinajs.cn/list="
SPOT_SYMBOL = "sh000300"


@dataclass(frozen=True)
class SpotQuote:
    symbol: str
    name: str
    last: float
    open: float
    prev_close: float
    high: float
    low: float
    volume: float
    turnover: float
    quote_date: str
    quote_time: str


@dataclass(frozen=True)
class FutureQuote:
    symbol: str
    contract_code: str
    name: str
    last: float
    open: float
    high: float
    low: float
    volume: float
    turnover: float
    open_interest: float
    avg_price: float | None
    quote_date: str
    quote_time: str
    is_main: bool
    expiry_date: str
    days_to_expiry: int


@dataclass(frozen=True)
class BasisRow:
    contract_code: str
    is_main: bool
    expiry_date: str
    days_to_expiry: int
    futures_price: float
    spot_price: float
    basis_points: float
    basis_rate_pct: float
    annualized_basis_pct: float | None
    open_interest: float
    carry_hint: str
    quote_date: str
    quote_time: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="粗看沪深300现货与 IF 合约的升贴水、基差和年化基差。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "text"),
        default="markdown",
        help="输出格式。",
    )
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="只输出主力合约。",
    )
    parser.add_argument(
        "--date",
        help="指定观察日期 YYYY-MM-DD；默认取今天，仅影响合约月份和剩余到期天数推导。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP 请求超时秒数。",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="跳过 TLS 证书校验；适合本机证书链异常时使用。",
    )
    parser.add_argument(
        "--output",
        help="把结果写入文件，而不是输出到 stdout。",
    )
    return parser.parse_args()


def parse_observation_date(raw_value: str | None) -> date:
    if raw_value:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    return date.today()


def add_months(value: date, months: int) -> date:
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    return date(year, month, 1)


def third_friday(year: int, month: int) -> date:
    fridays = [
        day
        for week in Calendar(firstweekday=0).monthdatescalendar(year, month)
        for day in week
        if day.month == month and day.weekday() == 4
    ]
    return fridays[2]


def listed_contract_months(observation_date: date) -> list[date]:
    current_month = date(observation_date.year, observation_date.month, 1)
    current_expiry = third_friday(current_month.year, current_month.month)
    effective_current = current_month if observation_date <= current_expiry else add_months(current_month, 1)

    listed = [effective_current, add_months(effective_current, 1)]

    cursor = add_months(effective_current, 2)
    while len(listed) < 4:
        if cursor.month in (3, 6, 9, 12):
            listed.append(cursor)
        cursor = add_months(cursor, 1)
    return listed


def contract_symbol(month_date: date) -> str:
    return f"nf_IF{month_date:%y%m}"


def fetch_raw_quotes(symbols: list[str], timeout: int, insecure: bool) -> tuple[dict[str, str], bool]:
    def do_fetch(skip_verify: bool) -> dict[str, str]:
        request = Request(
            SINA_QUOTE_URL + ",".join(symbols),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            },
        )
        context = ssl._create_unverified_context() if skip_verify else None
        with urlopen(request, timeout=timeout, context=context) as response:
            body = response.read().decode("gbk", "ignore")

        quotes: dict[str, str] = {}
        for line in body.splitlines():
            match = re.match(r'^var hq_str_(?P<symbol>[^=]+)="(?P<data>.*)";$', line.strip())
            if not match:
                continue
            quotes[match.group("symbol")] = match.group("data")
        return quotes

    if insecure:
        return do_fetch(True), True

    try:
        return do_fetch(False), False
    except URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        return do_fetch(True), True


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def require_fields(fields: list[str], minimum: int, symbol: str) -> None:
    if len(fields) < minimum:
        raise ValueError(f"{symbol} quote fields are incomplete: expected at least {minimum}, got {len(fields)}")


def parse_spot_quote(symbol: str, raw_value: str) -> SpotQuote:
    if not raw_value:
        raise ValueError(f"{symbol} returned empty quote data")

    fields = raw_value.split(",")
    require_fields(fields, 32, symbol)
    return SpotQuote(
        symbol=symbol,
        name=fields[0],
        open=parse_float(fields[1]),
        prev_close=parse_float(fields[2]),
        last=parse_float(fields[3]),
        high=parse_float(fields[4]),
        low=parse_float(fields[5]),
        volume=parse_float(fields[8]),
        turnover=parse_float(fields[9]),
        quote_date=fields[30],
        quote_time=fields[31],
    )


def parse_future_quote(symbol: str, raw_value: str, observation_date: date) -> FutureQuote:
    if not raw_value:
        raise ValueError(f"{symbol} returned empty quote data")

    fields = raw_value.split(",")
    require_fields(fields, 50, symbol)
    contract_code = symbol.removeprefix("nf_")
    contract_month = datetime.strptime(contract_code.removeprefix("IF"), "%y%m").date().replace(day=1)
    expiry = third_friday(contract_month.year, contract_month.month)
    return FutureQuote(
        symbol=symbol,
        contract_code=contract_code,
        name=fields[49] or contract_code,
        open=parse_float(fields[0]),
        high=parse_float(fields[1]),
        low=parse_float(fields[2]),
        last=parse_float(fields[3]),
        volume=parse_float(fields[4]),
        turnover=parse_float(fields[5]),
        open_interest=parse_float(fields[6]),
        avg_price=parse_float(fields[48]) if fields[48] else None,
        quote_date=fields[36],
        quote_time=fields[37],
        is_main=fields[39] == "1",
        expiry_date=expiry.isoformat(),
        days_to_expiry=(expiry - observation_date).days,
    )


def choose_main_contract(quotes: list[FutureQuote]) -> str | None:
    for quote in quotes:
        if quote.is_main:
            return quote.contract_code
    if not quotes:
        return None
    return max(quotes, key=lambda quote: quote.open_interest).contract_code


def carry_hint(basis_points: float) -> str:
    if basis_points < 0:
        return "贴水，偏负carry"
    if basis_points > 0:
        return "升水，偏正carry"
    return "平水"


def build_basis_rows(spot: SpotQuote, futures: list[FutureQuote]) -> list[BasisRow]:
    main_contract = choose_main_contract(futures)
    rows: list[BasisRow] = []
    for quote in sorted(futures, key=lambda item: (item.expiry_date, item.contract_code)):
        basis_points = quote.last - spot.last
        basis_rate_pct = (basis_points / spot.last) * 100 if spot.last else 0.0
        annualized_basis_pct = None
        if spot.last and quote.days_to_expiry > 0:
            annualized_basis_pct = basis_rate_pct * 365 / quote.days_to_expiry
        rows.append(
            BasisRow(
                contract_code=quote.contract_code,
                is_main=quote.contract_code == main_contract,
                expiry_date=quote.expiry_date,
                days_to_expiry=quote.days_to_expiry,
                futures_price=quote.last,
                spot_price=spot.last,
                basis_points=basis_points,
                basis_rate_pct=basis_rate_pct,
                annualized_basis_pct=annualized_basis_pct,
                open_interest=quote.open_interest,
                carry_hint=carry_hint(basis_points),
                quote_date=quote.quote_date,
                quote_time=quote.quote_time,
            )
        )
    return rows


def render_markdown(spot: SpotQuote, rows: list[BasisRow], used_insecure: bool, observation_date: date) -> str:
    lines = [
        "# 沪深300 IF 升贴水快照",
        "",
        f"- 观察日期：{observation_date.isoformat()}",
        f"- 现货时间戳：{spot.quote_date} {spot.quote_time}",
        f"- 沪深300现货：{spot.last:.4f}",
        f"- 口径：基差 = 期货 - 现货；贴水对多现货/空 IF 偏负 carry",
    ]
    if used_insecure:
        lines.append("- 网络说明：本次请求因证书链问题自动改为不校验 TLS 证书")
    lines.extend(
        [
            "",
            "| 合约 | 主力 | 到期日(近似) | 剩余天数 | 期货价 | 基差点数 | 基差率 | 年化基差率 | 持仓量 | 提示 |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in rows:
        annualized = f"{row.annualized_basis_pct:.2f}%" if row.annualized_basis_pct is not None else "-"
        lines.append(
            "| {contract} | {main} | {expiry} | {days} | {future:.4f} | {basis:.4f} | {rate:.2f}% | {annualized} | {oi:.0f} | {hint} |".format(
                contract=row.contract_code,
                main="是" if row.is_main else "",
                expiry=row.expiry_date,
                days=row.days_to_expiry,
                future=row.futures_price,
                basis=row.basis_points,
                rate=row.basis_rate_pct,
                annualized=annualized,
                oi=row.open_interest,
                hint=row.carry_hint,
            )
        )
    return "\n".join(lines)


def render_text(spot: SpotQuote, rows: list[BasisRow], used_insecure: bool, observation_date: date) -> str:
    lines = [
        f"观察日期: {observation_date.isoformat()}",
        f"现货时间戳: {spot.quote_date} {spot.quote_time}",
        f"沪深300现货: {spot.last:.4f}",
        "口径: 基差 = 期货 - 现货；贴水对多现货/空 IF 偏负 carry",
    ]
    if used_insecure:
        lines.append("网络说明: 本次请求因证书链问题自动改为不校验 TLS 证书")
    lines.append("")
    for row in rows:
        annualized = f"{row.annualized_basis_pct:.2f}%" if row.annualized_basis_pct is not None else "-"
        lines.append(
            f"{row.contract_code} {'[主力]' if row.is_main else ''} "
            f"到期={row.expiry_date} 剩余={row.days_to_expiry}天 "
            f"期货={row.futures_price:.4f} 基差={row.basis_points:.4f} "
            f"基差率={row.basis_rate_pct:.2f}% 年化={annualized} "
            f"持仓={row.open_interest:.0f} {row.carry_hint}"
        )
    return "\n".join(lines)


def render_json(spot: SpotQuote, rows: list[BasisRow], used_insecure: bool, observation_date: date) -> str:
    payload = {
        "observation_date": observation_date.isoformat(),
        "used_insecure_tls": used_insecure,
        "spot": asdict(spot),
        "rows": [asdict(row) for row in rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_output(content: str, output_path: str | None) -> None:
    if output_path:
        path = Path(output_path)
        path.write_text(content + "\n", encoding="utf-8")
        return
    sys.stdout.write(content + "\n")


def main() -> int:
    args = parse_args()
    observation_date = parse_observation_date(args.date)
    contract_months = listed_contract_months(observation_date)
    symbols = [SPOT_SYMBOL, *[contract_symbol(month_date) for month_date in contract_months]]

    try:
        raw_quotes, used_insecure = fetch_raw_quotes(symbols, timeout=args.timeout, insecure=args.insecure)
        spot = parse_spot_quote(SPOT_SYMBOL, raw_quotes.get(SPOT_SYMBOL, ""))
        futures = [
            parse_future_quote(symbol, raw_quotes.get(symbol, ""), observation_date)
            for symbol in symbols
            if symbol != SPOT_SYMBOL
        ]
    except (HTTPError, URLError, ValueError) as exc:
        sys.stderr.write(f"抓取 IF 基差失败: {exc}\n")
        return 1

    rows = build_basis_rows(spot, futures)
    if args.main_only:
        rows = [row for row in rows if row.is_main]

    if args.format == "json":
        content = render_json(spot, rows, used_insecure, observation_date)
    elif args.format == "text":
        content = render_text(spot, rows, used_insecure, observation_date)
    else:
        content = render_markdown(spot, rows, used_insecure, observation_date)

    write_output(content, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
