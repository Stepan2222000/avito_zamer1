# Implementation Plan: Асинхронный обработчик объявлений Avito

**Branch**: `001-async-avito-parser` | **Date**: 20 октября 2025 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification из `specs/001-async-avito-parser/spec.md`

## Summary

Построить асинхронный раннер на Python (Playwright + asyncpg), который читает ID объявлений из файла, распределяет их через общую очередь между воркерами и стабильно обрабатывает каждую карточку, строго следуя результатам `detect_page_state`. Основной акцент – KISS: один процесс, минимальный набор модулей, долговечные сессии браузера, кольцевой пул прокси и UPSERT в `new.listings` с автосозданием схемы/таблицы при старте; все параметры запуска задаются в `.env` и загружаются через `config.py`, программа стартует простой командой `python -m src.runner`.

## Technical Context

**Language/Version**: Python 3.13 (CPython)  
**Primary Dependencies**: Playwright (Python bindings), asyncpg, pydantic-settings (для KISS-конфига)  
**Storage**: PostgreSQL 17 (`avito_zamer`, схема `new`)  
**Testing**: Терминальные прогонки сценариев (без отдельной тестовой кодовой базы)  
**Target Platform**: Linux server / macOS dev (CLI)  
**Project Type**: Single-process CLI сервис  
**Performance Goals**: Обработка до 5 000 ID за ≤30 минут при параллели 4–6 воркеров  
**Constraints**: Минимум логов и метрик, стабильность ручного запуска, отсутствие внешних очередей, конфигурация только через `.env` + `config.py` (никаких CLI-аргументов)  
**Scale/Scope**: MVP одного процесса, ≤10 модулей Python, ≤6 одновременно активных воркеров

## Constitution Check

Конституция проекта содержит только заглушки и не накладывает формальных ограничений. Нарушений принципов KISS нет: план предусматривает один процесс, простую файловую конфигурацию и минимум вспомогательных абстракций.

## Project Structure

### Documentation (this feature)

```
specs/001-async-avito-parser/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    └── cli.md
```

### Source Code (repository root)

```
src/
├── __init__.py
├── config.py                # загрузка путей и параметров (items.txt, proxies, БД)
├── db.py                    # подключение asyncpg, миграция схемы new.listings
├── queue.py                 # асинхронная очередь задач и ре-кьюинг с внутренним lock от гонок
├── proxy_pool.py            # кольцевой пул, потокобезопасный доступ, чтение proxies.txt, запись blocked_proxies.txt
├── worker.py                # реализация цикла воркера и детекторного стейт-машина
└── runner.py                # точка входа CLI, запуск пула воркеров и итоговая сводка счётчиков

```

**Structure Decision**: Выбран один модуль `src/` с плоскими файлами, чтобы сохранить простоту (KISS) и сфокусироваться на воркере и очереди. Счётчики и метрики реализуются внутри `runner.py`, без отдельного модуля. Автотестов в отдельных файлах не планируется; проверки выполняются вручную через терминальные сценарии.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| *Н/Д* | *План укладывается в KISS, дополнительных усложнений нет* | *Н/Д* |
