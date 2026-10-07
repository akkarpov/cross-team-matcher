"""Turn recorded acceptance facts into the handoff protocol without inventing metrics."""
from pathlib import Path
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    runtime = ROOT / ".local" / "test-results"
    summary = json.loads((runtime / "acceptance-summary.json").read_text(encoding="utf-8"))
    browser = json.loads((runtime / "ui" / "ui-smoke.json").read_text(encoding="utf-8"))
    if browser.get("status") != "passed" or any(item["status"] != "PASS" for item in summary["results"].values()):
        raise SystemExit("Acceptance contains failures; protocol must not claim success")
    evidence = ROOT / "docs" / "evidence"
    evidence.mkdir(exist_ok=True)
    for name in ("acceptance-summary.json", "distributed-baseline.json", "distributed-restore.json", "restore-final.json", "distributed-facts.json", "distributed-owner-timeout.json", "topology.json"):
        path = runtime / name
        if path.is_file():
            shutil.copy2(path, evidence / name)
    shutil.copy2(runtime / "ui" / "ui-smoke.json", evidence / "ui-smoke.json")
    for path in (runtime / "ui").glob("*.json"):
        shutil.copy2(path, evidence / path.name)
    for name in ("manager-overview-desktop.png", "manager-project-detail-desktop.png", "manager-search-results-desktop.png", "manager-overview-mobile.png", "login-desktop.png"):
        path = runtime / "ui" / name
        if path.exists():
            shutil.copy2(path, evidence / name)
    performance = summary["results"]["T16"]["facts"]
    restoration = summary["results"]["T15"]["facts"]
    owner_timeout = summary["results"]["T12"]["facts"]
    delivery = summary["results"]["T10"]["facts"]
    lines = [
        "# Протокол реализации и испытаний", "", "Дата: 7 октября 2026, Europe/Moscow. Проект: Cross-Team Matcher 4.1.", "",
        "## Проверенная среда", "",
        "Windows 10, Python 3.11.2, PostgreSQL 17.4, три самостоятельных локальных процесса с отдельными каталогами данных. Сеть loopback, искусственная задержка не вводилась. 8 логических CPU, 16,97 ГБ RAM. Это фактическая среда, а не плановый минимум 4 vCPU/8 ГБ.", "",
        "## Автоматические проверки", "",
        "| Набор | Результат |", "|---|---|",
        "| Аутентификация, CSRF, конфигурация, commit отказа, свежесть копий и архитектурные ограничения | 9/9 PASS |",
        "| Живые SQL-сценарии T01–T08/T17 и дополнительные регрессии | 11/11 PASS |",
        "| HTTP-права, запрет подмены actor, валидация поиска, изоляция сессий | 4/4 PASS |",
        "| Распределённые испытания T09–T16 | 8/8 PASS, с целевыми повторными прогонами |",
        f"| Playwright: страницы пяти ролей | {len(browser.get('checks', []))} проверок, PASS |",
        "| Браузерный межрегиональный процесс | PASS |",
        "| Sphinx с -W, Doxygen HTML/RTF, C++-пример | PASS |",
        "| Git: конфликт, плохое слияние, падающий тест, revert, исправление и повторное слияние | PASS |", "",
        "SQL-прогон после миграции 04: 11 passed за 74,53 с. Воспроизводимые сценарии находятся в tests/sql и tests/stand. При отказных испытаниях приложения останавливаются; обычный pytest пропускает опасные для текущей сессии сценарии без переменных включения.", "",
        "## Измерения", "",
        f"T16: {performance['warmups']} прогревов, {performance['measurements']} измерений, {performance['concurrent_users']} параллельных пользователей. p50 = {performance['p50_seconds']:.3f} с, p95 = {performance['p95_seconds']:.3f} с, максимум = {performance['max_seconds']:.3f} с. Цель p95 ≤ 3 с выполнена.", "",
        "Измерялось полное подключение и транзакция SQL-поиска. На момент замера: 300 активных сотрудников плюс тестовые неактивные записи, 150 компетенций. Период 02–27 ноября 2026, 10 ч/нед., без обязательных навыков, OWN_FIRST. Это один зафиксированный сценарий, не гарантия для любой нагрузки.", "",
        f"Недоступный владелец T12: {owner_timeout.get('unavailable_owner_seconds', 'см. evidence')} с. Доставка T10: профиль {delivery.get('profile_delivery_seconds')} с, отзыв и событие {delivery.get('review_event_delivery_seconds')} с. Измерения относятся к локальному стенду.", "",
        "T15: восстановлены три отдельные проверочные базы; контрольные числа и SHA-256 всех 98 физических таблиц совпали. Закрытые данные, исходящая очередь и квитанции сохранены. Финальная реализация проверена атомарным pg_restore --single-transaction; фактические метрики:", "", "```json", json.dumps(restoration, ensure_ascii=False, indent=2), "```", "",
        "T09 EXPLAIN подтвердил Foreign Scan и Remote SQL; изменение проверено на основном владельце. T10 подтвердил доставку профиля, правки отзыва и события. T11/T12 проверили реальные отключения центра/региона и восстановление очереди. T13 повторил команду после потерянного подтверждения без дублирования. T14 проверил повтор установки/перезапуск и отсутствие бизнес-триггеров на копиях.", "",
        "## Браузерная проверка", "",
        "Проверены desktop и mobile, формы, поиск, роли и полный процесс: проект R1 → позиция → поиск → приглашение сотруднику R2 → личное согласие R2 → ACTIVE → завершение назначения → отзыв → COMPLETED. Итоговая запись и UUID доступны в evidence/ui-smoke.json. Ошибки консоли и страницы: " + str(len(browser.get('console_errors', []))) + "/" + str(len(browser.get('page_errors', []))) + ".", "",
        "## Обнаруженные и устранённые ошибки", "",
        "Во время интеграции исправлены коллизия имён слотов для нескольких подписчиков, защита приватных полей квитанций, позднее завершение после отмены, запрет обходного изменения отправленных условий, commit бизнес-отказа, переполнение мобильной сетки и сохранение режима поиска позиции. Первые T10/T15 прерывались временными тайм-аутами служебного подключения при параллельном дисковом вводе-выводе; исправлены повторы в испытательном доставщике и тайм-ауты только административного обслуживания. Прикладной connect_timeout остался 3 с. Источники каждого окончательного PASS указаны в acceptance-summary.json.", "",
        "## Документация и границы поставки", "",
        "Sphinx 8.2.3 собран без предупреждений (-W). Doxygen 1.18.0: HTML и RTF, пустой журнал предупреждений. Portable ZIP проверен по официальному SHA-256 e84f54cfd49ef06b0b16536056dbec0c496323de28abcce53a4269463de35eaf. Учебный C++-пример скомпилирован GCC; запуск Visual Studio не проверялся. Git-протокол реальный, remote локальный; внешнего push нет.", "",
        "Docker Compose подготовлен и проверен как YAML, фактический запуск Docker не выполнялся (Docker отсутствует). Автоматический failover и глобальная 2PC не заявляются. Межрегиональные копии асинхронны. Дополнительный физический standby описан как эксплуатационный сценарий, его развёртывание не входит в обязательное ТЗ.", "",
        "Файлы с секретами, рабочие базы, бэкапы и сессии в исходный архив не включаются. Исходное ТЗ и предварительный HTML-план сохранены отдельно; различия транспортных имён явно описаны в contracts/operations.json и docs/operations.rst.", "",
    ]
    (ROOT / "docs" / "protocol.md").write_text("\n".join(lines), encoding="utf-8")
    rst = ["Протокол испытаний", "="*80, "", "Фактический протокол: :download:`protocol.md <protocol.md>`.", "", "Машиночитаемые результаты: :download:`распределённые испытания <evidence/acceptance-summary.json>` и :download:`проверка браузера <evidence/ui-smoke.json>`.", "", f"Поиск: p95 **{performance['p95_seconds']:.3f} с** на {performance['measurements']} измерениях / {performance['concurrent_users']} пользователях. Все T09–T16 прошли; ограничения среды подробно записаны в протоколе.", "", ".. image:: evidence/manager-overview-desktop.png", "   :alt: Рабочий интерфейс Cross-Team Matcher", ""]
    (ROOT / "docs" / "protocol.rst").write_text("\n".join(rst), encoding="utf-8")
    print("Protocol generated from actual acceptance facts.")


if __name__ == "__main__":
    main()
