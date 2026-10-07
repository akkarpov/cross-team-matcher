/** @file Employee.h
 * @brief Учебная модель сотрудника для демонстрации Doxygen и C++.
 */
#pragma once
#include <string>

/** @brief Сотрудник с подтверждённым недельным бюджетом.
 * Поддерживает учебный расчёт свободных часов. В основном приложении
 * расчёт выполняется по каждому рабочему дню внутри PostgreSQL.
 */
class Employee {
public:
    /** @brief Создать модель.
     * @param nickname Рабочее имя без закрытых контактных сведений.
     * @param capacity Подтверждённый бюджет, от 0 до 40 часов.
     * @throws std::invalid_argument При недопустимом бюджете или имени.
     */
    Employee(std::string nickname, double capacity);
    virtual ~Employee() = default;
    /** @brief Получить свободные часы.
     * @param assigned Уже подтверждённая нагрузка, неотрицательная.
     * @return Оставшийся бюджет; отрицательный результат обозначает конфликт.
     */
    double available(double assigned) const;
    /** @brief Рабочее имя.
     * @return Ссылка на никнейм сотрудника.
     */
    const std::string& nickname() const;
protected:
    std::string nickname_; ///< Рабочий никнейм, не закрытое полное имя.
    double capacity_; ///< Подтверждённый недельный бюджет в часах.
};

/** @brief Сотрудник с неизменяемым домашним регионом.
 * Наследует бюджет и имя Employee, дополняет модель владельцем данных.
 */
class RegionalEmployee : public Employee {
public:
    /** @brief Создать регионального сотрудника.
     * @param nickname Рабочее имя.
     * @param capacity Недельный бюджет.
     * @param region Домашний регион: 1 или 2.
     */
    RegionalEmployee(std::string nickname, double capacity, int region);
    /** @brief Прочитать владельца.
     * @return Номер домашнего региона.
     */
    int region() const;
private:
    int region_; ///< Владелец основной записи сотрудника.
};
