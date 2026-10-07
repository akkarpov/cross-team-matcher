"use strict";

const filterButtons = [...document.querySelectorAll("[data-filter]")];
const milestones = [...document.querySelectorAll(".milestone")];
filterButtons.forEach(button => button.addEventListener("click", () => {
  const filter = button.dataset.filter;
  filterButtons.forEach(item => {
    const selected = item === button;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
  milestones.forEach(item => {
    item.hidden = filter !== "all" && item.dataset.owner !== filter && item.dataset.owner !== "all";
  });
  document.querySelector("#filter-status").textContent = `Показано этапов: ${milestones.filter(item => !item.hidden).length}. Общие этапы включены.`;
}));

const flows = {
  all: ["Три вида связи, разные гарантии.", "Справочники и рабочие копии приходят асинхронно. Изменения основных строк и доставка команд идут к владельцу через внешние секции."],
  catalog: ["Центр владеет общими справочниками.", "cat.regions и cat.competencies публикуются из C в R1 и R2. Регион читает локальную копию; изменение справочника выполняется в центре."],
  replica: ["Копии — для чтения, не для подтверждения назначения.", "R1 и R2 обмениваются рабочими данными напрямую и передают их в C. search читает локальные данные и копии, analytics на C — копии регионов. Закрытые контакты, аккаунты, outbox и квитанции не публикуются."],
  command: ["Команда доходит до основного владельца.", "Worker использует свой локальный DSN и global.processed_commands. PostgreSQL выбирает внешнюю секцию через postgres_fdw. Владелец атомарно сохраняет результат и квитанцию; отдельная транзакция подтверждает локальную доставку."],
};
document.querySelectorAll("[data-flow]").forEach(button => button.addEventListener("click", () => {
  const flow = button.dataset.flow;
  document.querySelectorAll("[data-flow]").forEach(item => {
    const selected = item === button;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
  document.querySelectorAll("[data-lane]").forEach(lane => lane.classList.toggle("dimmed", flow !== "all" && lane.dataset.lane !== flow));
  const description = document.querySelector("#flow-description");
  const title = document.createElement("b");
  title.textContent = flows[flow][0] + " ";
  description.replaceChildren(title, document.createTextNode(flows[flow][1]));
  document.querySelector(".architecture-diagram").dataset.activeFlow = flow;
}));

const steps = [
  {location: "НА УЗЛЕ МЕНЕДЖЕРА · R1", from: "R1", to: "search", route: "читает локально →", title: "Сначала — подходящие люди", description: "Менеджер задаёт навыки, период и часы. Поиск читает свои основные данные и копии другого региона, проверяет все обязательные навыки и доступность на каждый рабочий день.", code: "GET /api/search → search.*", guarantee: "Попадание в выдачу не создаёт назначение и не резервирует часы."},
  {location: "НА ВЛАДЕЛЬЦЕ ПРОЕКТА · R1", from: "Проект", to: "outbox", route: "одна транзакция →", title: "Условия становятся офертой", description: "Менеджер отправляет приглашение. Сохраняются снимок условий, его хеш и сообщение OFFER в локальном outbox. После отправки условия не меняются обходным редактированием.", code: "invitation + terms_snapshot + private.outbox / OFFER", guarantee: "Приглашение и исходящая команда фиксируются вместе. Часы сотрудника ещё свободны."},
  {location: "ДОСТАВЩИК · R1 → R2", from: "R1", to: "R2", route: "OFFER · postgres_fdw →", title: "Сообщение доходит до владельца", description: "Worker арендует команду в outbox и вставляет её в global.processed_commands. Внешняя секция направляет запись в R2. Обработчик R2 фиксирует изменение и квитанцию одной транзакцией; R1 отмечает доставку отдельно.", code: "INSERT global.processed_commands → route → владелец R2", guarantee: "Потерянный ответ допускает повтор с тем же ID и хешем. Квитанция защищает от дублирования."},
  {location: "НА УЗЛЕ СОТРУДНИКА · R2", from: "Сотрудник", to: "R2", route: "личное согласие →", title: "Согласие проверяет календарь", description: "Сотрудник входит на домашнем узле и принимает предложение. SQL проверяет роль, версию условий и свободные часы на каждый рабочий день. Обновление schedule_version сериализует конкурирующие изменения календаря.", code: "согласие → schedule_version → проверка нагрузки → assignment", guarantee: "Менеджер не может согласиться за сотрудника. Два подтверждения не занимают одни последние часы."},
  {location: "ОТВЕТ ВЛАДЕЛЬЦУ ПРОЕКТА · R2 → R1", from: "R2", to: "R1", route: "RESULT · квитанция →", title: "Проект получает подтверждение", description: "На R2 согласие и назначение уже сохранены. Ответная команда RESULT доходит до владельца проекта в R1. Проект учитывает подтверждённое участие по результату владельца, а не по предположению из поисковой копии.", code: "R2 / outbox → RESULT → R1 / processed_commands", guarantee: "Если владелец недоступен, команда остаётся в очереди. Неподтверждённое участие не выдаётся за назначение."},
  {location: "ПОДТВЕРЖДЁННОЕ ЗАВЕРШЕНИЕ · R1 ↔ R2", from: "R1", to: "R2", route: "COMPLETE → / ← RESULT", title: "Отзыв — после завершения", description: "Менеджер завершает участие; команда COMPLETE доходит до владельца сотрудника. После квитанции завершения открывается отзыв с оценкой и комментарием. Редактирование отзыва сохраняет версию и событие истории.", code: "COMPLETE → подтверждение COMPLETED → review + workflow_events", guarantee: "Поздняя команда не оживляет отменённое участие. Отзыв нельзя открыть до подтверждённого завершения."},
];
let currentStep = 0;
function selectStep(index) {
  currentStep = index;
  const step = steps[index];
  for (const [id, key] of [["step-location", "location"], ["route-from", "from"], ["route-to", "to"], ["route-label", "route"], ["step-title", "title"], ["step-description", "description"], ["step-code", "code"], ["step-guarantee", "guarantee"]]) {
    document.getElementById(id).textContent = step[key];
  }
  document.querySelector("#step-counter").textContent = `0${index + 1} / 06`;
  document.querySelectorAll("[data-step]").forEach(button => {
    const selected = Number(button.dataset.step) === index;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  document.querySelector("#next-step").textContent = index === steps.length - 1 ? "К первому шагу ↺" : "Следующий шаг →";
}
document.querySelectorAll("[data-step]").forEach(button => button.addEventListener("click", () => selectStep(Number(button.dataset.step))));
document.querySelector("#next-step").addEventListener("click", () => selectStep((currentStep + 1) % steps.length));

const dialog = document.querySelector("#image-dialog");
let imageTrigger;
document.querySelectorAll(".screenshot-open").forEach(button => button.addEventListener("click", () => {
  imageTrigger = button;
  document.querySelector("#image-title").textContent = button.dataset.title;
  document.querySelector("#full-image").src = button.dataset.image;
  document.querySelector("#full-image").alt = button.dataset.title;
  dialog.showModal();
}));
document.querySelector("#close-image").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", event => {
  const bounds = dialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
});
dialog.addEventListener("close", () => imageTrigger?.focus());

const printDetails = [...document.querySelectorAll("details")];
let savedDetails = [];
window.addEventListener("beforeprint", () => {
  savedDetails = printDetails.map(detail => detail.open);
  printDetails.forEach(detail => { detail.open = true; });
});
window.addEventListener("afterprint", () => {
  printDetails.forEach((detail, index) => { detail.open = savedDetails[index] ?? false; });
});
document.querySelector("#print-report").addEventListener("click", () => window.print());

const navigationLinks = [...document.querySelectorAll(".site-header nav a")];
if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      navigationLinks.forEach(link => {
        const selected = link.hash === `#${entry.target.id}`;
        link.classList.toggle("current", selected);
        if (selected) link.setAttribute("aria-current", "location");
        else link.removeAttribute("aria-current");
      });
    });
  }, {rootMargin: "-12% 0px -65% 0px"});
  navigationLinks.forEach(link => observer.observe(document.querySelector(link.hash)));
}
