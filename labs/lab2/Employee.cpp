/** @file Employee.cpp
 * @brief Реализация учебных Employee и RegionalEmployee.
 */
#include "Employee.h"
#include <stdexcept>
#include <utility>
#include <cmath>
Employee::Employee(std::string nickname, double capacity)
    : nickname_(std::move(nickname)), capacity_(capacity) {
    if (nickname_.empty() || !std::isfinite(capacity) || capacity < 0 || capacity > 40)
        throw std::invalid_argument("invalid employee capacity");
}
double Employee::available(double assigned) const {
    if (!std::isfinite(assigned) || assigned < 0) throw std::invalid_argument("negative load");
    return capacity_ - assigned;
}
const std::string& Employee::nickname() const { return nickname_; }
RegionalEmployee::RegionalEmployee(std::string nickname, double capacity, int region)
    : Employee(std::move(nickname), capacity), region_(region) {
    if (region != 1 && region != 2) throw std::invalid_argument("invalid region");
}
int RegionalEmployee::region() const { return region_; }
