"""Stable public errors; database details are never returned to the browser."""

MESSAGES = {
    "forbidden": "Недостаточно прав для этого действия.",
    "payload_invalid": "Проверьте заполнение полей и допустимые значения.",
    "owner_unavailable": "Узел владельца временно недоступен. Действие не подтверждено; повторите позже.",
    "stale_replica": "Копия данных ещё не готова или отстаёт от источника.",
    "command_conflict": "Этот идентификатор команды уже использован с другими условиями.",
    "capacity_exceeded": "Недостаточно доступных часов на один или несколько рабочих дней.",
    "terms_mismatch": "Условия приглашения изменились. Обновите страницу и проверьте новую версию.",
    "review_not_allowed": "Отзыв доступен только менеджеру после подтверждённого завершения участия.",
    "version_conflict": "Запись уже изменена. Обновите страницу перед сохранением.",
    "not_found": "Запись не найдена.",
    "unauthorized": "Войдите в систему.",
    "csrf_invalid": "Сессия формы устарела. Обновите страницу.",
}


class DomainError(Exception):
    """A safe, machine-readable error exposed by the HTTP and SQL adapters.

    :param code: A stable contract error identifier.
    :param status: HTTP response status.
    """

    def __init__(self, code: str, status: int = 400):
        super().__init__(MESSAGES.get(code, MESSAGES["payload_invalid"]))
        self.code = code
        self.status = status

