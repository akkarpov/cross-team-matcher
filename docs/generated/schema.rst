cat.competencies
----------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``competency_id``
     - uuid
     - нет
     - —
   * - ``name``
     - text
     - нет
     - —
   * - ``kind``
     - text
     - нет
     - —
   * - ``description``
     - text
     - нет
     - —
   * - ``active``
     - boolean
     - нет
     - —
   * - ``updated_at``
     - timestamp with time zone
     - нет
     - —

Ограничения::

   CHECK ((kind = ANY (ARRAY['SKILL'::text, 'TOOL'::text])))
   CHECK (((length(btrim(name)) >= 1) AND (length(btrim(name)) <= 100)))
   UNIQUE (name)
   PRIMARY KEY (competency_id)

cat.regions
-----------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``region_id``
     - smallint
     - нет
     - —
   * - ``code``
     - text
     - нет
     - —
   * - ``name``
     - text
     - нет
     - —
   * - ``active``
     - boolean
     - нет
     - —

Ограничения::

   UNIQUE (code)
   PRIMARY KEY (region_id)

global.assignments
------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``assignment_id``
     - uuid
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``invitation_id``
     - uuid
     - нет
     - —
   * - ``project_region``
     - smallint
     - нет
     - —
   * - ``project_id``
     - uuid
     - нет
     - —
   * - ``position_id``
     - uuid
     - нет
     - —
   * - ``terms_version``
     - bigint
     - нет
     - —
   * - ``date_from``
     - date
     - нет
     - —
   * - ``date_to``
     - date
     - нет
     - —
   * - ``hours_per_week``
     - numeric(4,1)
     - нет
     - —
   * - ``status``
     - text
     - нет
     - —
   * - ``updated_at``
     - timestamp with time zone
     - нет
     - —

Ограничения::

   CHECK ((date_from <= date_to))
   CHECK (((hours_per_week > (0)::numeric) AND (hours_per_week <= (40)::numeric) AND (mod(hours_per_week, 0.5) = (0)::numeric)))
   CHECK ((status = ANY (ARRAY['ACTIVE'::text, 'CONFLICT'::text, 'CANCELLED'::text, 'COMPLETED'::text])))

global.capacity_periods
-----------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``id``
     - uuid
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``date_from``
     - date
     - нет
     - —
   * - ``date_to``
     - date
     - нет
     - —
   * - ``hours_per_week``
     - numeric(4,1)
     - нет
     - —

Ограничения::

   CHECK ((date_from <= date_to))
   CHECK (((hours_per_week > (0)::numeric) AND (hours_per_week <= (40)::numeric) AND (mod(hours_per_week, 0.5) = (0)::numeric)))

global.employee_competencies
----------------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``competency_id``
     - uuid
     - нет
     - —
   * - ``verified_at``
     - timestamp with time zone
     - нет
     - —

global.employees
----------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``nickname``
     - text
     - нет
     - —
   * - ``experience_months``
     - integer
     - нет
     - —
   * - ``active``
     - boolean
     - нет
     - —
   * - ``schedule_version``
     - bigint
     - нет
     - —
   * - ``updated_at``
     - timestamp with time zone
     - нет
     - —

Ограничения::

   CHECK (((experience_months >= 0) AND (experience_months <= 1200)))
   CHECK (((length(btrim(nickname)) >= 1) AND (length(btrim(nickname)) <= 100)))

global.invitation_responses
---------------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``invitation_id``
     - uuid
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``project_region``
     - smallint
     - нет
     - —
   * - ``project_id``
     - uuid
     - нет
     - —
   * - ``position_id``
     - uuid
     - нет
     - —
   * - ``decision``
     - text
     - нет
     - —
   * - ``terms_version``
     - bigint
     - нет
     - —
   * - ``terms_snapshot``
     - jsonb
     - нет
     - —
   * - ``terms_hash``
     - text
     - нет
     - —
   * - ``decision_at``
     - timestamp with time zone
     - да
     - —
   * - ``assignment_id``
     - uuid
     - да
     - —
   * - ``source_version``
     - bigint
     - нет
     - —
   * - ``updated_at``
     - timestamp with time zone
     - нет
     - —

Ограничения::

   CHECK ((decision = ANY (ARRAY['PENDING'::text, 'ACCEPTED'::text, 'DECLINED'::text, 'CANCELLED'::text, 'REJECTED'::text, 'REPLACED'::text, 'COMPLETED'::text])))

global.invitations
------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``invitation_id``
     - uuid
     - нет
     - —
   * - ``position_id``
     - uuid
     - нет
     - —
   * - ``employee_region``
     - smallint
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``terms_version``
     - bigint
     - нет
     - —
   * - ``terms_snapshot``
     - jsonb
     - нет
     - —
   * - ``terms_hash``
     - text
     - нет
     - —
   * - ``status``
     - text
     - нет
     - —
   * - ``assignment_id``
     - uuid
     - да
     - —
   * - ``assignment_status``
     - text
     - да
     - —
   * - ``replaces_invitation_id``
     - uuid
     - да
     - —
   * - ``version``
     - bigint
     - нет
     - —
   * - ``created_at``
     - timestamp with time zone
     - нет
     - —
   * - ``updated_at``
     - timestamp with time zone
     - нет
     - —

Ограничения::

   CHECK ((employee_region = ANY (ARRAY[1, 2])))
   CHECK ((status = ANY (ARRAY['PENDING'::text, 'ACCEPTED'::text, 'DECLINED'::text, 'CANCELLING'::text, 'CANCELLED'::text, 'REJECTED'::text, 'REPLACED'::text, 'COMPLETED'::text])))

global.position_competencies
----------------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``position_id``
     - uuid
     - нет
     - —
   * - ``competency_id``
     - uuid
     - нет
     - —

global.processed_commands
-------------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``command_id``
     - uuid
     - нет
     - —
   * - ``source_region``
     - smallint
     - нет
     - —
   * - ``actor_id``
     - uuid
     - да
     - —
   * - ``kind``
     - text
     - нет
     - —
   * - ``payload_hash``
     - text
     - нет
     - —
   * - ``payload``
     - jsonb
     - нет
     - —
   * - ``result``
     - jsonb
     - да
     - —
   * - ``processed_at``
     - timestamp with time zone
     - нет
     - —

global.project_positions
------------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``position_id``
     - uuid
     - нет
     - —
   * - ``project_id``
     - uuid
     - нет
     - —
   * - ``title``
     - text
     - нет
     - —
   * - ``min_experience``
     - integer
     - нет
     - —
   * - ``hours_per_week``
     - numeric(4,1)
     - нет
     - —
   * - ``search_mode``
     - text
     - нет
     - —
   * - ``version``
     - bigint
     - нет
     - —
   * - ``updated_at``
     - timestamp with time zone
     - нет
     - —

Ограничения::

   CHECK (((hours_per_week > (0)::numeric) AND (hours_per_week <= (40)::numeric) AND (mod(hours_per_week, 0.5) = (0)::numeric)))
   CHECK (((min_experience >= 0) AND (min_experience <= 1200)))
   CHECK ((search_mode = ANY (ARRAY['OWN_FIRST'::text, 'OWN_ONLY'::text, 'ALL'::text])))
   CHECK (((length(btrim(title)) >= 1) AND (length(btrim(title)) <= 120)))

global.projects
---------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``project_id``
     - uuid
     - нет
     - —
   * - ``manager_id``
     - uuid
     - нет
     - —
   * - ``name``
     - text
     - нет
     - —
   * - ``description``
     - text
     - нет
     - —
   * - ``date_from``
     - date
     - нет
     - —
   * - ``date_to``
     - date
     - нет
     - —
   * - ``status``
     - text
     - нет
     - —
   * - ``version``
     - bigint
     - нет
     - —
   * - ``updated_at``
     - timestamp with time zone
     - нет
     - —

Ограничения::

   CHECK ((date_from <= date_to))
   CHECK (((length(btrim(name)) >= 1) AND (length(btrim(name)) <= 150)))
   CHECK ((status = ANY (ARRAY['FORMING'::text, 'READY'::text, 'ACTIVE'::text, 'CANCELLING'::text, 'CANCELLED'::text, 'COMPLETED'::text])))

global.reviews
--------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``review_id``
     - uuid
     - нет
     - —
   * - ``project_id``
     - uuid
     - нет
     - —
   * - ``employee_region``
     - smallint
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``assignment_id``
     - uuid
     - нет
     - —
   * - ``author_id``
     - uuid
     - нет
     - —
   * - ``rating``
     - integer
     - нет
     - —
   * - ``comment``
     - text
     - нет
     - —
   * - ``version``
     - bigint
     - нет
     - —
   * - ``created_at``
     - timestamp with time zone
     - нет
     - —
   * - ``updated_at``
     - timestamp with time zone
     - нет
     - —

Ограничения::

   CHECK (((length(btrim(comment)) >= 1) AND (length(btrim(comment)) <= 2000)))
   CHECK (((rating >= 1) AND (rating <= 5)))

global.unavailability_periods
-----------------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``id``
     - uuid
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``date_from``
     - date
     - нет
     - —
   * - ``date_to``
     - date
     - нет
     - —
   * - ``active``
     - boolean
     - нет
     - —

Ограничения::

   CHECK ((date_from <= date_to))

global.workflow_events
----------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``event_id``
     - uuid
     - нет
     - —
   * - ``object_type``
     - text
     - нет
     - —
   * - ``object_id``
     - uuid
     - нет
     - —
   * - ``object_version``
     - bigint
     - нет
     - —
   * - ``actor_id``
     - uuid
     - да
     - —
   * - ``event_type``
     - text
     - нет
     - —
   * - ``occurred_at``
     - timestamp with time zone
     - нет
     - —
   * - ``details``
     - jsonb
     - нет
     - —

private.account_roles
---------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``account_id``
     - uuid
     - нет
     - —
   * - ``role_code``
     - text
     - нет
     - —
   * - ``scope_id``
     - text
     - нет
     - —

Ограничения::

   FOREIGN KEY (account_id) REFERENCES private.accounts(account_id)
   PRIMARY KEY (account_id, role_code, scope_id)
   CHECK ((role_code = ANY (ARRAY['EMPLOYEE'::text, 'EDITOR'::text, 'MANAGER'::text, 'CATALOG_ADMIN'::text, 'ANALYST'::text])))

private.accounts
----------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``account_id``
     - uuid
     - нет
     - —
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``employee_id``
     - uuid
     - да
     - —
   * - ``login``
     - text
     - нет
     - —
   * - ``password_hash``
     - text
     - нет
     - —
   * - ``active``
     - boolean
     - нет
     - —

Ограничения::

   UNIQUE (login)
   CHECK ((owner_region = private.node_region()))
   FOREIGN KEY (owner_region, employee_id) REFERENCES r1_data.employees(owner_region, employee_id)
   PRIMARY KEY (account_id)

private.employee_private
------------------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``owner_region``
     - smallint
     - нет
     - —
   * - ``employee_id``
     - uuid
     - нет
     - —
   * - ``full_name``
     - text
     - да
     - —
   * - ``contact_email``
     - text
     - да
     - —
   * - ``contact_phone``
     - text
     - да
     - —

Ограничения::

   CHECK ((owner_region = private.node_region()))
   FOREIGN KEY (owner_region, employee_id) REFERENCES r1_data.employees(owner_region, employee_id)
   PRIMARY KEY (owner_region, employee_id)

private.outbox
--------------



.. list-table:: Колонки
   :header-rows: 1

   * - Имя
     - Тип
     - NULL
     - Описание
   * - ``message_id``
     - uuid
     - нет
     - —
   * - ``target_region``
     - smallint
     - нет
     - —
   * - ``command_id``
     - uuid
     - нет
     - —
   * - ``kind``
     - text
     - нет
     - —
   * - ``payload``
     - jsonb
     - нет
     - —
   * - ``actor_id``
     - uuid
     - да
     - —
   * - ``state``
     - text
     - нет
     - —
   * - ``attempts``
     - integer
     - нет
     - —
   * - ``next_attempt_at``
     - timestamp with time zone
     - нет
     - —
   * - ``created_at``
     - timestamp with time zone
     - нет
     - —
   * - ``delivered_at``
     - timestamp with time zone
     - да
     - —
   * - ``last_error``
     - text
     - да
     - —

Ограничения::

   PRIMARY KEY (message_id)
   CHECK ((state = ANY (ARRAY['PENDING'::text, 'DELIVERED'::text])))
   CHECK ((target_region = ANY (ARRAY[1, 2])))
   UNIQUE (target_region, command_id)
