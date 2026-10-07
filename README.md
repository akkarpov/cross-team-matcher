# Cross-Team Matcher

[Отчёт о проекте](https://akkarpov.github.io/cross-team-matcher/) ·
[Документация](https://akkarpov.github.io/cross-team-matcher/docs/) ·
[История версий](CHANGELOG.md) · [Разработка втроём](docs/development.rst)

Самостоятельный проект по ТЗ 4.1: подбор сотрудников разных регионов, приглашения, личное согласие, календарь нагрузки, назначения, отзывы и центральная аналитика. В комплект входят материалы лабораторной № 2 по Git и Doxygen.

**Стек:** Python 3.11 / FastAPI / Jinja / CSS и JavaScript, PostgreSQL 17, `postgres_fdw`, штатная логическая репликация. Три экземпляра приложения, по одному DSN у каждого. В приложении нет диспетчера региональных адресов. Интерфейс на русском языке, адаптирован для компьютера и телефона.

## Быстрый запуск на Windows

Нужен установленный PostgreSQL 17. По умолчанию используется `C:\Program Files\PostgreSQL\17\bin`; другой путь передаётся через `init --pg-bin`.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python scripts/local.py init
.\.venv\Scripts\python scripts/local.py db-start
.\.venv\Scripts\python scripts/local.py setup
.\.venv\Scripts\python scripts/local.py seed
.\.venv\Scripts\python scripts/local.py start
```

| Экземпляр | Адрес |
|---|---|
| Регион R1 | http://127.0.0.1:8101 |
| Регион R2 | http://127.0.0.1:8102 |
| Центральный офис | http://127.0.0.1:8100 |

Логины и сгенерированные пароли: `.local/demo-credentials.json`. Учебные аккаунты: `manager.r1`, `editor.r1`, `employee.r1`, аналогичные для R2, `admin.c`, `analyst.c`. Секреты не входят в исходную поставку. Все данные вымышлены. Основной набор — 300 сотрудников, 150 компетенций, 8 проектов и 24 позиции; тесты добавляют отдельные синтетические записи.

`python scripts/local.py status` — состояние; `stop` — остановка только процессов этого стенда. Данные остаются в `.local`. Для возобновления: `db-start`, затем `start`. Не удаляйте `.local`, если хотите сохранить созданные записи.

Docker Compose, резервные копии и восстановление описаны в [deploy/README.md](deploy/README.md). Compose подготовлен, но его запуск не проверен на данном компьютере: Docker отсутствует.

## Функции

- Пять ролей с серверной проверкой прав и локальной аутентификацией.
- Профили, подтверждённые компетенции, закрытые контакты, доступность и отсутствия.
- Проекты и позиции, три режима поиска, все обязательные навыки и календарная проверка до пагинации.
- Межрегиональные приглашения, согласие самого сотрудника, повторные условия, отмена и завершение.
- Очередь исходящих команд, атомарные квитанции, идемпотентность, история изменений.
- Отзывы после подтверждённого завершения, версии и история правок.
- Аналитика из копий и отображение наблюдаемого состояния репликации.

## Документация и лабораторная

На GitHub Pages опубликован отчёт: зоны ответственности Карпова, Лебедева и
Кречуна по первоначальному плану, этапы реализации, интерактивная схема обмена,
путь приглашения, скриншоты и результаты испытаний. История Git берётся из
реальных коммитов при сборке; этапы не подменяют авторство и даты Git.

`python scripts/build_pages.py` собирает отчёт и Sphinx в `dist/pages`.
`python scripts/check_pages.py` проверяет ссылки, мобильную верстку и интерактивность.
Workflow `pages.yml` публикует сайт после push в `main`; в PR выполняет проверки.

```powershell
python scripts/build_docs.py --schema
python scripts/build_docs.py --doxygen
python scripts/lab2_git_demo.py
```

Sphinx: `docs/_build/html/index.html` (также `/docs` из приложения). Doxygen: `docs/_build/doxygen/html/index.html` и `docs/_build/doxygen/rtf/refman.rtf`. Для второй команды нужен Doxygen в PATH или переносимая версия в `.work/tools/doxygen`.

[Материалы лабораторной](docs/lab2.rst) включают C++-пример для Visual Studio/CMake в `labs/lab2`, документированные базовый и производный классы и реальный Git-сценарий в изолированных учебных репозиториях. [Протокол](docs/protocol.md) фиксирует результаты, а [первичная оценка](docs/assessment.md) — выбор архитектуры и границы проекта.

## Проверки

```powershell
python -m pytest tests/test_auth.py tests/test_architecture.py tests/test_freshness.py -q
$env:MATCHER_HTTP_TESTS = '1'
python -m pytest tests/test_http.py -q
python scripts/browser_check.py
```

При первой установке Playwright загрузите браузер: `python -m playwright install chromium`.

SQL и отказные испытания требуют отдельного прогона без фоновых приложений: порядок и переменные включения приведены в [deploy/README.md](deploy/README.md). Они временно выключают узлы учебного стенда. Перед защитой ознакомьтесь с [матрицей испытаний](docs/testing.rst).

## Структура

`app/` — приложение и доставщик; `sql/` — схема, workflow, представления и последовательные миграции; `replication/` — топология FDW и подписок; `deploy/` — контейнеры и эксплуатация; `contracts/` — команды и ошибки; `tests/` — проверки; `docs/` — исходная документация; `labs/lab2/` — отдельный пример из методички.


Исходное ТЗ: [Google Docs, редакция 4.1](https://docs.google.com/document/d/1hJzW6u7EndforUwxPeMt6Hbnaq3CJilYWin5Lmo7Ao8/edit). Архив Department Control Center изучен как справочный материал; его код, дизайн и инфраструктура в новый проект не переносились.
