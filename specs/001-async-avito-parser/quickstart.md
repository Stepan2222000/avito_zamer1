# Quickstart — Асинхронный обработчик объявлений Avito

## Предусловия
1. Python 3.13 и Poetry/venv установлены.
2. Playwright браузеры установлены (`playwright install chromium`).
3. Доступ к PostgreSQL: host `81.30.105.134`, port `5402`, database `avito_zamer`, user `admin`, password `root`.
4. Файл `.env` в корне проекта содержит пути к `data/items.txt`, `data/proxies.txt`, параметры БД и числа воркеров (пример лежит в репозитории).

### Настройка `.env`
```bash
cd /Users/stepanorlov/Desktop/STARTED/onezamer_v2/onezamer
cp .env .env.local  # при необходимости
# отредактируйте .env или .env.local, обновив пути и доступы
```
Если файл отсутствует, приложение использует значения по умолчанию из `src/config.py`.

## Установка
```bash
cd /Users/stepanorlov/Desktop/STARTED/onezamer_v2
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
# при необходимости скорректируйте значения в .env перед запуском
```

## Запуск обработки
```bash
cd onezamer
python -m src.runner
```

### Что делает команда
1. Загружает конфигурацию из `config.py` и создаёт схему `new` + таблицу `new.listings`, если они отсутствуют.
2. Загружает список прокси, инициализирует кольцевой пул.
3. Создаёт глобальный пул соединений asyncpg и очередь задач.
4. Запускает N воркеров (указано в конфиге); каждый воркер берёт ID из очереди, вызывает `detect_page_state` и действует по регламенту из спецификации.
5. По завершении выводит краткую сводку (успехи, блокировки, капчи).

### Проверка User Story 1
1. Подготовьте `data/items.txt` с тестовыми ID (например, 3–5 валидных ID Avito).
2. Запустите обработку командой `python -m src.runner`.
3. По завершении убедитесь, что в логах присутствуют события `task_success` или `task_missing` для каждого ID и итоговая строка `runner_finished`.
4. Подключитесь к БД `avito_zamer` и выполните запрос:
   ```sql
   SELECT item_id, status, failure_reason, processed_at
   FROM new.listings
   WHERE item_id IN (... список проверяемых ID ...);
   ```
   Проверьте, что статусы равны `success` либо `unavailable`, а `failure_reason` пуст для успешных карточек.
5. Если в процессе встречались капчи или блокировки, откройте `data/blocked_proxies.txt` и логи — там должны быть события `captcha_failed`/`proxy_block` с указанием прокси.
6. При необходимости повторите запуск: удалите строки из `blocked_proxies.txt`, обновите `items.txt` и снова выполните шаги 2–4.

## Завершение и отладка
- Для остановки используйте `Ctrl+C`: runner завершит активные задачи, закроет страницы и освободит прокси.
- Заблокированные прокси проверяются в `data/blocked_proxies.txt`; удалите строку, чтобы вернуть прокси в работу.
- Минимальные метрики выводятся в консоль: `processed`, `unavailable`, `blocked`, `retries`.

## Восстановление после блокировок и сбоев
1. **Следите за логами.**  
   - `proxy_blocked` — текущий прокси получил 403/407 и перенесён в `data/blocked_proxies.txt`.  
   - `queue_paused reason=no_proxy_available` — все прокси временно заблокированы; воркеры ждут разблокировки.
2. **Разблокируйте прокси вручную.**  
   Откройте `data/blocked_proxies.txt`, удалите строки с восстановленными адресами и сохраните файл.
3. **Возобновите обработку.**  
   Программа автоматически перечитает файл, снимет паузу (`queue_resumed`) и продолжит работу. Если runner был остановлен, перезапустите его командой:
   ```bash
   cd /Users/stepanorlov/Desktop/STARTED/onezamer_v2/onezamer
   python -m src.runner
   ```
4. **Повторный запуск после падения рабочего процесса.**  
   После любого критического сбоя (сообщения `worker_error` или аварийный `KeyboardInterrupt`) убедитесь, что БД и прокси доступны, затем снова выполните команду запуска. Очередь автоматически подберёт оставшиеся ID, повторно обрабатывать уже завершённые задачи не требуется.
