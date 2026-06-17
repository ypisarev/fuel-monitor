from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import os
import re
import sqlite3
import smtplib
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_RECIPIENT = "yar.pisarev11@gmail.com"
DEFAULT_DB_PATH = "fuel_monitor.sqlite3"
DEFAULT_TIMEZONE = "Europe/Kyiv"
DEFAULT_SCHEDULE = "09:00"
DEFAULT_BASE_FUEL_PRICE = 79.99
DEFAULT_ALERT_THRESHOLD = 0.05
DEFAULT_HISTORY_DAYS = 5
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FuelMonitor/1.0"


@dataclass(slots=True)
class FuelRow:
    name: str
    price: str
    change: str | None = None
    change_percent: str | None = None


@dataclass(slots=True)
class FuelSnapshot:
    source_name: str
    url: str
    title: str
    updated_at: str | None
    diesel: FuelRow


@dataclass(slots=True)
class HistoryPoint:
    source_name: str
    day: dt.date
    price: float


@dataclass(slots=True)
class RunSchedule:
    expression: str
    kind: str
    hour: int | None = None
    minute: int | None = None
    interval_minutes: int | None = None


def fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="ignore")


def clean_text(value: str) -> str:
    value = html_lib.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("\u00ad", "")
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return clean_text(match.group(1))


def extract_main_table(html_text: str) -> str:
    table_match = re.search(
        r"<table[^>]*class=['\"]line['\"][^>]*>.*?<caption>.*?Средние цены.*?</caption>(.*?)</table>",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        raise ValueError("Не удалось найти таблицу цен на странице")
    return table_match.group(1)


def extract_rows(table_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.IGNORECASE | re.DOTALL):
        cells = [clean_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.IGNORECASE | re.DOTALL)]
        if cells:
            rows.append(cells)
    return rows


def parse_price_row(cells: list[str]) -> FuelRow | None:
    normalized_cells = [cell for cell in cells if cell]
    if not normalized_cells:
        return None

    fuel_name = normalized_cells[0]
    if re.search(r"Вид топлива", fuel_name, re.IGNORECASE):
        return None

    numeric_cells = [cell for cell in normalized_cells[1:] if re.search(r"\d", cell)]
    if not numeric_cells:
        return None

    price = numeric_cells[0]
    change = numeric_cells[1] if len(numeric_cells) > 1 else None
    change_percent = numeric_cells[2] if len(numeric_cells) > 2 else None
    return FuelRow(name=fuel_name, price=price, change=change, change_percent=change_percent)


def find_diesel_row(html_text: str) -> FuelRow:
    table_html = extract_main_table(html_text)
    for cells in extract_rows(table_html):
        row = parse_price_row(cells)
        if row and "дизел" in row.name.lower():
            return row
    raise ValueError("Не удалось найти строку с дизельным топливом")


def parse_snapshot(source_name: str, url: str) -> FuelSnapshot:
    html_text = fetch_html(url)
    title = first_match(r"<title>(.*?)</title>", html_text) or source_name
    updated_at = first_match(r"последнее обновление:\s*([^<]+)", html_text)
    diesel = find_diesel_row(html_text)
    return FuelSnapshot(
        source_name=source_name,
        url=url,
        title=title,
        updated_at=updated_at,
        diesel=diesel,
    )


def build_message(
        snapshots: list[FuelSnapshot],
        history_by_source: dict[str, list[HistoryPoint]],
        base_price: float,
        alert_threshold: float,
        history_days: int,
        recipient: str,
        sender: str,
) -> EmailMessage:
        subject_date = snapshots[0].updated_at or "сейчас"
        alert_state = "alert" if any(
                abs(float(snapshot.diesel.price.replace(",", ".")) - base_price) / base_price > alert_threshold
                for snapshot in snapshots
        ) else "ok"
        subject_prefix = "ALERT" if alert_state == "alert" else "OK"
        subject = f"{subject_prefix}: дизель {subject_date}"

        text_lines = [f"Статус: {'ВНИМАНИЕ' if alert_state == 'alert' else 'ОК'}", "", "Текущие цены на дизельное топливо:"]
        for snapshot in snapshots:
                price_value = float(snapshot.diesel.price.replace(",", "."))
                state, deviation, deviation_ratio = calculate_alert_state(price_value, base_price, alert_threshold)
                parts = [
                        f"{snapshot.source_name}: {snapshot.diesel.price} грн/л",
                        f"отклонение {deviation:+.2f} грн ({deviation_ratio * 100:.2f}%)",
                ]
                if snapshot.diesel.change is not None:
                        parts.append(f"изменение {snapshot.diesel.change}")
                if snapshot.updated_at:
                        parts.append(f"обновлено {snapshot.updated_at}")
                parts.append("ALERT" if state == "alert" else "OK")
                text_lines.append("; ".join(parts))
        text_lines.extend(["", "Источники:"])
        text_lines.extend(f"- {snapshot.source_name}: {snapshot.url}" for snapshot in snapshots)

        summary_cards = []
        for snapshot in snapshots:
                price_value = float(snapshot.diesel.price.replace(",", "."))
                state, deviation, deviation_ratio = calculate_alert_state(price_value, base_price, alert_threshold)
                badge_bg = "#dcfce7" if state == "ok" else "#fee2e2"
                badge_fg = "#166534" if state == "ok" else "#991b1b"
                card_border = "#86efac" if state == "ok" else "#fca5a5"
                summary_cards.append(
                        f"""
                        <div style='border:1px solid {card_border};border-radius:16px;padding:14px 16px;background:#fff;'>
                            <div style='display:flex;justify-content:space-between;gap:10px;align-items:center;'>
                                <div style='font-weight:700;font-size:16px;color:#0f172a;'>{escape(snapshot.source_name)}</div>
                                <div style='padding:4px 10px;border-radius:999px;background:{badge_bg};color:{badge_fg};font-size:12px;font-weight:700;'>{'ОК' if state == 'ok' else 'ТРЕВОГА'}</div>
                            </div>
                            <div style='margin-top:8px;font-size:28px;font-weight:800;color:#0f172a;'>{escape(snapshot.diesel.price)} <span style='font-size:14px;font-weight:600;color:#475569;'>грн/л</span></div>
                            <div style='margin-top:6px;font-size:13px;color:#475569;'>Отклонение от 79.99: {deviation:+.2f} грн ({deviation_ratio * 100:.2f}%)</div>
                        </div>
                        """
                )

        chart_svg = build_history_chart_svg(history_by_source, base_price, history_days)
        top_banner_bg = "linear-gradient(135deg,#166534,#22c55e)" if alert_state == "ok" else "linear-gradient(135deg,#7f1d1d,#ef4444)"
        top_banner_text = "Все ок" if alert_state == "ok" else "Внимание: отклонение больше 5%"
        top_banner_note = "Текущие цены в норме." if alert_state == "ok" else "Одна или несколько цен вышли за допустимый диапазон."

        html_body = f"""
        <html>
            <body style='margin:0;padding:0;background:#f8fafc;font-family:Arial,Helvetica,sans-serif;color:#0f172a;'>
                <div style='max-width:820px;margin:0 auto;padding:24px;'>
                    <div style='background:{top_banner_bg};color:#fff;border-radius:18px;padding:24px 28px;margin-bottom:20px;'>
                        <div style='font-size:13px;letter-spacing:.08em;text-transform:uppercase;opacity:.85;'>Fuel monitor</div>
                        <h1 style='margin:10px 0 0;font-size:28px;line-height:1.2;'>{top_banner_text}</h1>
                        <p style='margin:10px 0 0;font-size:15px;opacity:.92;'>{top_banner_note}</p>
                    </div>
                    <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;'>
                        {''.join(summary_cards)}
                    </div>
                    {chart_svg}
                    <div style='margin-top:18px;padding:14px 16px;background:#fff;border:1px solid #e2e8f0;border-radius:16px;font-size:13px;color:#475569;'>
                        <div><strong>Кому:</strong> {escape(recipient)}</div>
                        <div><strong>От:</strong> {escape(sender)}</div>
                        <div style='margin-top:6px;'><strong>База:</strong> {base_price:.2f} грн/л; <strong>Порог:</strong> {alert_threshold * 100:.0f}%</div>
                    </div>
                    <div style='margin-top:16px;font-size:13px;color:#64748b;'>Источники: {''.join(f"<a href='{escape(snapshot.url)}' style='color:#1d4ed8;text-decoration:none;'>{escape(snapshot.source_name)}</a>{' · ' if i < len(snapshots)-1 else ''}" for i, snapshot in enumerate(snapshots))}</div>
                </div>
            </body>
        </html>
        """

        message = EmailMessage()
        message["To"] = recipient
        message["From"] = sender
        message["Subject"] = subject
        message.set_content("\n".join(text_lines))
        message.add_alternative(html_body, subtype="html")
        return message


def ensure_database(db_path: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fuel_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                source_name TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                updated_at TEXT,
                fuel_name TEXT NOT NULL,
                price TEXT NOT NULL,
                change_value TEXT,
                change_percent TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_fuel_snapshots_captured_at ON fuel_snapshots(captured_at)"
        )
        connection.commit()


def store_snapshots(db_path: str, snapshots: list[FuelSnapshot]) -> None:
    ensure_database(db_path)
    captured_at = dt.datetime.now(dt.timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO fuel_snapshots (
                captured_at,
                source_name,
                url,
                title,
                updated_at,
                fuel_name,
                price,
                change_value,
                change_percent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    captured_at,
                    snapshot.source_name,
                    snapshot.url,
                    snapshot.title,
                    snapshot.updated_at,
                    snapshot.diesel.name,
                    snapshot.diesel.price,
                    snapshot.diesel.change,
                    snapshot.diesel.change_percent,
                )
                for snapshot in snapshots
            ],
        )
        connection.commit()


def load_history_points(db_path: str, timezone: ZoneInfo, history_days: int) -> dict[str, list[HistoryPoint]]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=history_days)
    latest_per_day: dict[tuple[str, dt.date], tuple[dt.datetime, float]] = {}

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT captured_at, source_name, price
            FROM fuel_snapshots
            WHERE captured_at >= ?
            ORDER BY captured_at ASC
            """,
            (cutoff.isoformat(),),
        ).fetchall()

    for captured_at_text, source_name, price_text in rows:
        captured_at = dt.datetime.fromisoformat(captured_at_text)
        local_day = captured_at.astimezone(timezone).date()
        price_value = float(str(price_text).replace(",", "."))
        key = (source_name, local_day)
        previous = latest_per_day.get(key)
        if not previous or captured_at > previous[0]:
            latest_per_day[key] = (captured_at, price_value)

    history_by_source: dict[str, list[HistoryPoint]] = {}
    for (source_name, day), (_, price_value) in latest_per_day.items():
        history_by_source.setdefault(source_name, []).append(
            HistoryPoint(source_name=source_name, day=day, price=price_value)
        )

    for points in history_by_source.values():
        points.sort(key=lambda point: point.day)

    return history_by_source


def calculate_alert_state(current_price: float, base_price: float, threshold: float) -> tuple[str, float, float]:
    deviation = current_price - base_price
    deviation_ratio = abs(deviation) / base_price
    state = "alert" if deviation_ratio > threshold else "ok"
    return state, deviation, deviation_ratio


def build_history_chart_svg(
    history_by_source: dict[str, list[HistoryPoint]],
    base_price: float,
    history_days: int,
) -> str:
    all_points = [point for points in history_by_source.values() for point in points]
    if not all_points:
        return ""

    ordered_days = sorted({point.day for point in all_points})[-history_days:]
    if not ordered_days:
        return ""

    values = [base_price] + [point.price for point in all_points]
    min_value = min(values)
    max_value = max(values)
    value_range = max(max_value - min_value, 1.0)
    min_value -= value_range * 0.15
    max_value += value_range * 0.15
    adjusted_range = max(max_value - min_value, 1.0)

    palette = {"Украина": "#2563eb", "Запорожская обл.": "#dc2626"}

    legend_items = []
    for source_name, color in palette.items():
        if source_name in history_by_source:
            legend_items.append(
                f"<div style='display:flex;align-items:center;gap:8px;margin-right:16px;'><span style='width:12px;height:12px;border-radius:999px;background:{color};display:inline-block;'></span><span>{escape(source_name)}</span></div>"
            )
    legend_items.append(
        "<div style='display:flex;align-items:center;gap:8px;'><span style='width:12px;height:12px;border-radius:999px;background:#16a34a;display:inline-block;'></span><span>Базовая цена 79.99</span></div>"
    )

    chart_rows = []
    chart_height = 190
    bar_area_height = 150

    baseline_ratio = (base_price - min_value) / adjusted_range
    baseline_ratio = max(0.0, min(1.0, baseline_ratio))
    baseline_top = (1 - baseline_ratio) * bar_area_height

    for source_name, color in palette.items():
        if source_name not in history_by_source:
            continue

        point_map = {point.day: point.price for point in history_by_source[source_name]}
        day_cells = []
        for day in ordered_days:
            price_value = point_map.get(day)
            if price_value is None:
                day_cells.append(
                    f"<div style='display:flex;flex-direction:column;align-items:center;justify-content:flex-end;flex:1;min-width:0;'>"
                    f"<div style='height:{bar_area_height}px;width:100%;'></div>"
                    f"<div style='font-size:11px;color:#94a3b8;margin-top:6px;'>{day.strftime('%d.%m')}</div>"
                    f"</div>"
                )
                continue

            normalized = (price_value - min_value) / adjusted_range
            normalized = max(0.0, min(1.0, normalized))
            bar_height = max(8, int(normalized * bar_area_height))
            price_label = f"{price_value:.2f}".replace(".", ",")
            day_cells.append(
                f"<div style='display:flex;flex-direction:column;align-items:center;justify-content:flex-end;flex:1;min-width:0;'>"
                f"<div style='display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:{bar_area_height}px;width:100%;position:relative;'>"
                f"<div style='position:absolute;top:{baseline_top:.1f}px;left:6px;right:6px;height:1px;border-top:1px dashed #16a34a;opacity:.8;'></div>"
                f"<div style='margin-bottom:6px;font-size:11px;font-weight:700;color:#0f172a;'>{price_label}</div>"
                f"<div style='width:22px;height:{bar_height}px;background:{color};border-radius:8px 8px 3px 3px;box-shadow:0 8px 16px rgba(15,23,42,.12);'></div>"
                f"</div>"
                f"<div style='font-size:11px;color:#64748b;margin-top:6px;'>{day.strftime('%d.%m')}</div>"
                f"</div>"
            )

        chart_rows.append(
            f"""
            <div style='border:1px solid #e2e8f0;border-radius:14px;padding:14px 14px 10px;background:#f8fafc;'>
              <div style='display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap;'>
                <div style='font-weight:700;color:#0f172a;'>{escape(source_name)}</div>
                <div style='font-size:12px;color:#475569;'>база {base_price:.2f} грн/л</div>
              </div>
              <div style='display:flex;align-items:flex-end;gap:8px;height:{chart_height}px;position:relative;'>
                <div style='position:absolute;left:0;right:0;top:{baseline_top:.1f}px;border-top:1px dashed #16a34a;opacity:.8;'></div>
                {''.join(day_cells)}
              </div>
            </div>
            """
        )

    return f"""
    <div style='background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:16px 16px 12px;margin-top:18px;'>
      <div style='display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px;flex-wrap:wrap;'>
        <div>
          <div style='font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:#64748b;'>History</div>
          <div style='font-size:18px;font-weight:700;color:#0f172a;'>Последние {history_days} дней</div>
        </div>
        <div style='display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:#475569;'>
          {''.join(legend_items)}
        </div>
      </div>
            <div style='display:grid;grid-template-columns:1fr;gap:12px;'>
                {''.join(chart_rows)}
            </div>
    </div>
    """


def load_smtp_settings() -> dict[str, str | int]:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("EMAIL_FROM", username)
    recipient = os.getenv("EMAIL_TO", DEFAULT_RECIPIENT)
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "sender": sender,
        "recipient": recipient,
        "use_tls": use_tls,
    }


def load_runtime_settings() -> tuple[str, ZoneInfo]:
    db_path = os.getenv("FUEL_DB_PATH", DEFAULT_DB_PATH)
    timezone_name = os.getenv("FUEL_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception as exc:
        raise SystemExit(f"Неверный часовой пояс FUEL_TIMEZONE={timezone_name}: {exc}") from exc
    return db_path, timezone


def parse_schedule(expression: str) -> RunSchedule:
    cleaned_expression = expression.strip()

    daily_match = re.fullmatch(r"(\d{1,2}):(\d{2})", cleaned_expression)
    if daily_match:
        hour = int(daily_match.group(1))
        minute = int(daily_match.group(2))
        if hour > 23 or minute > 59:
            raise SystemExit(f"Неверный FUEL_SCHEDULE={expression}: ожидается время в диапазоне 00:00-23:59")
        return RunSchedule(expression=cleaned_expression, kind="daily", hour=hour, minute=minute)

    interval_match = re.fullmatch(r"\*/(\d+)\s+\*\s+\*\s+\*\s+\*", cleaned_expression)
    if interval_match:
        interval_minutes = int(interval_match.group(1))
        if interval_minutes < 1:
            raise SystemExit(f"Неверный FUEL_SCHEDULE={expression}: интервал должен быть больше нуля")
        return RunSchedule(
            expression=cleaned_expression,
            kind="interval",
            interval_minutes=interval_minutes,
        )

    raise SystemExit(
        "Неверный FUEL_SCHEDULE. Используйте формат HH:MM для ежедневного запуска или '*/N * * * *' для интервала в минутах."
    )


def send_message(message: EmailMessage, smtp_settings: dict[str, str | int]) -> None:
    host = str(smtp_settings["host"])
    port = int(smtp_settings["port"])
    username = str(smtp_settings["username"])
    password = str(smtp_settings["password"])
    use_tls = bool(smtp_settings.get("use_tls", False))

    if (not username or not password) and host not in {"postfix", "localhost", "127.0.0.1"}:
        raise RuntimeError(
            "Для внешнего SMTP-сервера задайте SMTP_USER и SMTP_PASSWORD (для Gmail нужен app password)."
        )

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        if username and password:
            server.login(username, password)
        server.send_message(message)


def get_current_snapshots(sources: list[tuple[str, str]]) -> list[FuelSnapshot]:
    snapshots: list[FuelSnapshot] = []
    for source_name, url in sources:
        try:
            snapshots.append(parse_snapshot(source_name, url))
        except (URLError, ValueError) as exc:
            raise SystemExit(f"Не удалось получить данные со страницы {url}: {exc}") from exc
    return snapshots


def run_cycle(sources: list[tuple[str, str]], db_path: str, smtp_settings: dict[str, str | int]) -> None:
    snapshots = get_current_snapshots(sources)
    store_snapshots(db_path, snapshots)
    timezone = ZoneInfo(os.getenv("FUEL_TIMEZONE", DEFAULT_TIMEZONE))
    base_price = float(os.getenv("BASE_FUEL_PRICE", str(DEFAULT_BASE_FUEL_PRICE)))
    alert_threshold = float(os.getenv("FUEL_ALERT_THRESHOLD", str(DEFAULT_ALERT_THRESHOLD)))
    history_days = int(os.getenv("FUEL_HISTORY_DAYS", str(DEFAULT_HISTORY_DAYS)))
    history_by_source = load_history_points(db_path, timezone, history_days)

    sender = str(smtp_settings["sender"] or smtp_settings["username"] or "noreply@example.com")
    recipient = str(smtp_settings["recipient"])
    message = build_message(
        snapshots=snapshots,
        history_by_source=history_by_source,
        base_price=base_price,
        alert_threshold=alert_threshold,
        history_days=history_days,
        recipient=recipient,
        sender=sender,
    )
    send_message(message, smtp_settings)
    print(f"Письмо отправлено на {recipient}")


def sleep_until_next_run(schedule: RunSchedule, timezone: ZoneInfo) -> float:
    now = dt.datetime.now(timezone)

    if schedule.kind == "interval":
        interval_seconds = schedule.interval_minutes * 60
        current_timestamp = now.timestamp()
        next_run_timestamp = (int(current_timestamp) // interval_seconds + 1) * interval_seconds
        return max(0.0, next_run_timestamp - current_timestamp)

    next_run = now.replace(hour=schedule.hour or 0, minute=schedule.minute or 0, second=0, microsecond=0)
    if next_run <= now:
        next_run += dt.timedelta(days=1)
    return max(0.0, (next_run - now).total_seconds())


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor Minfin fuel prices and prepare an email report.")
    parser.add_argument("--send", action="store_true", help="Send the email instead of printing a preview.")
    parser.add_argument("--daemon", action="store_true", help="Run continuously according to FUEL_SCHEDULE.")
    args = parser.parse_args()

    sources = [
        ("Украина", "https://index.minfin.com.ua/markets/fuel/"),
        ("Запорожская обл.", "https://index.minfin.com.ua/markets/fuel/reg/zaporozhskaya/"),
    ]

    db_path, timezone = load_runtime_settings()
    schedule = parse_schedule(os.getenv("FUEL_SCHEDULE", DEFAULT_SCHEDULE))
    base_price = float(os.getenv("BASE_FUEL_PRICE", str(DEFAULT_BASE_FUEL_PRICE)))
    alert_threshold = float(os.getenv("FUEL_ALERT_THRESHOLD", str(DEFAULT_ALERT_THRESHOLD)))
    history_days = int(os.getenv("FUEL_HISTORY_DAYS", str(DEFAULT_HISTORY_DAYS)))
    smtp_settings = load_smtp_settings()

    if args.daemon:
        print(f"Запуск демона. Расписание {schedule.expression} ({timezone.key}).")
        while True:
            wait_seconds = sleep_until_next_run(schedule, timezone)
            time.sleep(wait_seconds)
            try:
                run_cycle(sources, db_path, smtp_settings)
            except Exception as exc:
                print(f"Ошибка при ежедневной отправке: {exc}", file=sys.stderr)
        return 0

    snapshots = get_current_snapshots(sources)
    store_snapshots(db_path, snapshots)
    history_by_source = load_history_points(db_path, timezone, history_days)
    sender = str(smtp_settings["sender"] or smtp_settings["username"] or "noreply@example.com")
    recipient = str(smtp_settings["recipient"])
    message = build_message(
        snapshots=snapshots,
        history_by_source=history_by_source,
        base_price=base_price,
        alert_threshold=alert_threshold,
        history_days=history_days,
        recipient=recipient,
        sender=sender,
    )

    if args.send:
        send_message(message, smtp_settings)
        print(f"Письмо отправлено на {recipient}")
        return 0

    print(message.get_body(preferencelist=("plain",)).get_content())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())