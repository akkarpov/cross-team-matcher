/** @file main.cpp
 * @brief Исполняемая проверка учебного примера для защиты лабораторной.
 */
#include "Employee.h"
#include <cassert>
#include <iostream>
int main() {
    RegionalEmployee employee("synthetic-engineer", 40, 1);
    assert(employee.available(24) == 16);
    assert(employee.region() == 1);
    assert(employee.available(48) == -8);
    std::cout << "Lab 2: inheritance and capacity checks passed\n";
}
