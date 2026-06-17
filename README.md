# Fuel Monitor

Небольшой Python-скрипт для мониторинга цен на дизельное топливо на страницах Minfin:

- https://index.minfin.com.ua/markets/fuel/
- https://index.minfin.com.ua/markets/fuel/reg/zaporozhskaya/

Скрипт умеет:

- вытягивать текущую цену дизеля с обеих страниц;
- сохранять историю в SQLite;
- строить график за последние 5 дней;
- подсвечивать отклонение от базовой цены 79.99 грн/л;
- собирать письмо с результатом;
- печатать письмо в консоль или отправлять его по SMTP.

## Запуск

Предпросмотр письма:

```bash
python fuel_monitor.py
```

Отправка письма:

```bash
python fuel_monitor.py --send
```

Запуск в фоне по расписанию из `FUEL_SCHEDULE`:

```bash
python fuel_monitor.py --daemon
```

Частоту запуска можно вынести в `.env` через `FUEL_SCHEDULE`.
Поддерживаются два формата:

- `09:00` - ежедневный запуск в заданное время;
- `*/5 * * * *` - запуск каждые 5 минут.

SQLite-база по умолчанию создается в `fuel_monitor.sqlite3` рядом со скриптом. В Docker она хранится в `/data/fuel_monitor.sqlite3`.

Если текущая цена отклоняется от `BASE_FUEL_PRICE` больше чем на 5%, письмо станет красным. Если все в пределах порога, статус и баннер будут зелеными.

## Docker

Сборка и запуск контейнера:

```bash
docker compose up -d --build
```

Контейнер будет ждать расписание из `FUEL_SCHEDULE` и отправлять письмо по нему.
Перед запуском заполните переменные в shell или в локальном `.env` файле рядом с `docker-compose.yml`.
Для теста сейчас можно оставить `FUEL_SCHEDULE=*/5 * * * *` в `.env`.
Отправка идет через Gmail SMTP, поэтому нужен app password.

## Настройка почты

Перед отправкой письма задайте переменные окружения:

- `SMTP_HOST` - по умолчанию `smtp.gmail.com`
- `SMTP_PORT` - по умолчанию `587`
- `SMTP_USER` - ваш адрес Gmail
- `SMTP_PASSWORD` - app password для Gmail
- `EMAIL_FROM` - адрес отправителя, по умолчанию `SMTP_USER`
- `EMAIL_TO` - адрес получателя, по умолчанию `yar.pisarev11@gmail.com`
- `FUEL_DB_PATH` - путь к SQLite базе, по умолчанию `fuel_monitor.sqlite3`
- `FUEL_TIMEZONE` - часовой пояс, по умолчанию `Europe/Kyiv`
- `FUEL_SCHEDULE` - расписание запуска, по умолчанию `09:00`
- `SMTP_USE_TLS` - должен быть `true` для Gmail
- `BASE_FUEL_PRICE` - базовая цена для сравнения, по умолчанию `79.99`
- `FUEL_ALERT_THRESHOLD` - порог отклонения, по умолчанию `0.05`
- `FUEL_HISTORY_DAYS` - глубина графика и выборки, по умолчанию `5`

Если нужна доставка через Gmail, `SMTP_PASSWORD` должен быть именно app password, а не обычный пароль.
# Fuel Monitor

Небольшой Python-скрипт для мониторинга цен на дизельное топливо на страницах Minfin:


Скрипт умеет:


## Запуск

Предпросмотр письма:

```bash
python fuel_monitor.py
```

Отправка письма:

```bash
python fuel_monitor.py --send
```

Запуск в фоне по расписанию из `FUEL_SCHEDULE`:

```bash
python fuel_monitor.py --daemon
```

Частоту запуска можно вынести в `.env` через `FUEL_SCHEDULE`.
Поддерживаются два формата:


SQLite-база по умолчанию создается в `fuel_monitor.sqlite3` рядом со скриптом. В Docker она хранится в `/data/fuel_monitor.sqlite3`.

## Docker

Сборка и запуск контейнера:

```bash
docker compose up -d --build
```

Контейнер будет ждать расписание из `FUEL_SCHEDULE` и отправлять письмо по нему.
Перед запуском заполните переменные в shell или в локальном `.env` файле рядом с `docker-compose.yml`.
Для теста сейчас можно оставить `FUEL_SCHEDULE=*/5 * * * *` в `.env`.
Отправка идет через Gmail SMTP, поэтому нужен app password.

## Настройка почты

Перед отправкой письма задайте переменные окружения:


`SMTP_USE_TLS` должен быть `true` для Gmail.

Если цена отклоняется от `BASE_FUEL_PRICE` больше чем на 5%, письмо станет красным. Если все в пределах порога, статус и баннер будут зелеными.
- `BASE_FUEL_PRICE` - базовая цена для сравнения, по умолчанию `79.99`
- `FUEL_ALERT_THRESHOLD` - порог отклонения, по умолчанию `0.05`
- `FUEL_HISTORY_DAYS` - глубина графика и выборки, по умолчанию `5`

Если нужна доставка через Gmail, `SMTP_PASSWORD` должен быть именно app password, а не обычный пароль.