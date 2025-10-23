# onezamer Development Guidelines

Auto-generated from all feature plans. Last updated: 2025-10-20

## Active Technologies
- Python 3.13 (CPython) + Playwright (Python bindings), asyncpg, pydantic-settings (для KISS-конфига) (001-async-avito-parser)

## Project Structure
```
src/
tests/
```

## Commands
cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style
Python 3.13 (CPython): Follow standard conventions

## Recent Changes
- 001-async-avito-parser: Added Python 3.13 (CPython) + Playwright (Python bindings), asyncpg, pydantic-settings (для KISS-конфига)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->

7) Качество кода
Соблюдать PEP 8/PEP 484, строгие типы (typing/mypy чисто).
Функции с docstring (краткая цель + контракт вход/выход/исключения).

В нашем проекте отдельные тесты не нужны, но агент должен все тестирвоать в терминале и проверять, а провильно ли все выполняется и рабоатет

Максимально стоит тестировать на практике (полноценно запуская программу и проверяя то, правильно ли она рабоатет)

MVP, KISS - ВСЕ ДОЛЖНО БЫТЬ ПРОСТЫМ

Обязательные правила (MVP, KISS)
1) Архитектура и границы
Держим один процесс на узле, asyncio + фиксированное число воркеров WORKERS; никаких внешних очередей.
Воркеры независимы: падение одного не влияет на другие; каждый сам себя перезапускает.
Любые изменения — не усложнять структуру: добавлять файлы/слои только при явной необходимости (KISS).
2) Playwright и навигация
Все HTTP-взаимодействие только через Playwright (никаких requests/aiohttp).
На воркер — одна долговечная страница; переиспользуем её между задачами.
После каждого page.goto обязательно вызывать detect_page_state(...) и ветвиться строго по его результату.

ГОВОРИ СО МНОЙ ТОЛЬКО ПО РУССКИ

НИКОГДА НЕ ВРИ МНЕ, ГОВОРИ ТОЛЬКО ПРАВДУ, ЕСЛИ ЧЕГО ТО НЕ ЗНАЕШЬ - ТАК И ГОВОРИ

ЕСЛИ ЕСТЬ ВОПРОСЫ - ЗАДАВАЙ


Всегда у кода добавляй множество коментариев, которые поясняют задачу


ВСЕГДА СМОТРИ НАПЕРЕД, СТАВЬ КОМЕНТАРИИ TODO И ОТМЕЧАЙ ФАЗУ, В КОТОРУЮ БУДЕМ РЕАЛИЗОВЫВАТЬ ЭТОТ ФУНКЦИОНАЛ, ПОМНИ, ЧТО И В КАКУЮ ФАЗУ НУЖНО РЕАЛИЗОВЫВАТЬ

ПРИ РАБОТЕ С САМЫМИ ВАЖНЫМИ БИБЛИОТЕКАМИ ПРОСМАТРИВАЙ ДОКУМЕНТАЦИИ ЧЕРЕЗ mcp context7


