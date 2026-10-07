\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE EXTENSION IF NOT EXISTS btree_gist;
SELECT set_config('matcher.install_region', :'node_region', false);
DO $$ BEGIN
  IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='matcher_app') THEN CREATE ROLE matcher_app LOGIN; END IF;
  IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='matcher_worker') THEN CREATE ROLE matcher_worker LOGIN; END IF;
  IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='transport_c') THEN CREATE ROLE transport_c LOGIN; END IF;
  IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='transport_r1') THEN CREATE ROLE transport_r1 LOGIN; END IF;
  IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='transport_r2') THEN CREATE ROLE transport_r2 LOGIN; END IF;
END $$;
CREATE SCHEMA cat;
CREATE SCHEMA global;
CREATE SCHEMA private;
CREATE SCHEMA route;
CREATE SCHEMA search;
CREATE SCHEMA analytics;
CREATE SCHEMA r1_data;
CREATE SCHEMA r2_data;
DO $$ BEGIN EXECUTE format('CREATE FUNCTION private.node_region() RETURNS smallint LANGUAGE sql IMMUTABLE AS %L', 'SELECT '||current_setting('matcher.install_region')||'::smallint'); END $$;
CREATE TABLE cat.regions(region_id smallint PRIMARY KEY,code text NOT NULL UNIQUE,name text NOT NULL,active boolean NOT NULL DEFAULT true);
CREATE TABLE cat.competencies(competency_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),name text NOT NULL UNIQUE CHECK(length(btrim(name)) BETWEEN 1 AND 100),kind text NOT NULL CHECK(kind IN ('SKILL','TOOL')),description text NOT NULL DEFAULT '',active boolean NOT NULL DEFAULT true,updated_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE global.employees(owner_region smallint NOT NULL,employee_id uuid NOT NULL DEFAULT gen_random_uuid(),nickname text NOT NULL CHECK(length(btrim(nickname)) BETWEEN 1 AND 100),experience_months integer NOT NULL DEFAULT 0 CHECK(experience_months BETWEEN 0 AND 1200),active boolean NOT NULL DEFAULT true,schedule_version bigint NOT NULL DEFAULT 0,updated_at timestamptz NOT NULL DEFAULT clock_timestamp()) PARTITION BY LIST(owner_region);
CREATE TABLE global.employee_competencies(owner_region smallint NOT NULL,employee_id uuid NOT NULL,competency_id uuid NOT NULL,verified_at timestamptz NOT NULL DEFAULT clock_timestamp()) PARTITION BY LIST(owner_region);
CREATE TABLE global.capacity_periods(owner_region smallint NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),employee_id uuid NOT NULL,date_from date NOT NULL,date_to date NOT NULL,hours_per_week numeric(4,1) NOT NULL CHECK(hours_per_week > 0 AND hours_per_week <= 40 AND mod(hours_per_week,0.5)=0),CHECK(date_from<=date_to)) PARTITION BY LIST(owner_region);
CREATE TABLE global.unavailability_periods(owner_region smallint NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),employee_id uuid NOT NULL,date_from date NOT NULL,date_to date NOT NULL,active boolean NOT NULL DEFAULT true,CHECK(date_from<=date_to)) PARTITION BY LIST(owner_region);
CREATE TABLE global.projects(owner_region smallint NOT NULL,project_id uuid NOT NULL DEFAULT gen_random_uuid(),manager_id uuid NOT NULL,name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 150),description text NOT NULL DEFAULT '',date_from date NOT NULL,date_to date NOT NULL,status text NOT NULL DEFAULT 'FORMING' CHECK(status IN ('FORMING','READY','ACTIVE','CANCELLING','CANCELLED','COMPLETED')),version bigint NOT NULL DEFAULT 1,updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),CHECK(date_from<=date_to)) PARTITION BY LIST(owner_region);
CREATE TABLE global.project_positions(owner_region smallint NOT NULL,position_id uuid NOT NULL DEFAULT gen_random_uuid(),project_id uuid NOT NULL,title text NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 120),min_experience integer NOT NULL DEFAULT 0 CHECK(min_experience BETWEEN 0 AND 1200),hours_per_week numeric(4,1) NOT NULL CHECK(hours_per_week>0 AND hours_per_week<=40 AND mod(hours_per_week,0.5)=0),search_mode text NOT NULL DEFAULT 'OWN_FIRST' CHECK(search_mode IN ('OWN_FIRST','OWN_ONLY','ALL')),version bigint NOT NULL DEFAULT 1,updated_at timestamptz NOT NULL DEFAULT clock_timestamp()) PARTITION BY LIST(owner_region);
CREATE TABLE global.position_competencies(owner_region smallint NOT NULL,position_id uuid NOT NULL,competency_id uuid NOT NULL) PARTITION BY LIST(owner_region);
CREATE TABLE global.invitations(owner_region smallint NOT NULL,invitation_id uuid NOT NULL DEFAULT gen_random_uuid(),position_id uuid NOT NULL,employee_region smallint NOT NULL CHECK(employee_region IN (1,2)),employee_id uuid NOT NULL,terms_version bigint NOT NULL DEFAULT 1,terms_snapshot jsonb NOT NULL,terms_hash text NOT NULL,status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','ACCEPTED','DECLINED','CANCELLING','CANCELLED','REJECTED','REPLACED','COMPLETED')),assignment_id uuid,assignment_status text,replaces_invitation_id uuid,version bigint NOT NULL DEFAULT 1,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp()) PARTITION BY LIST(owner_region);
CREATE TABLE global.invitation_responses(owner_region smallint NOT NULL,invitation_id uuid NOT NULL,employee_id uuid NOT NULL,project_region smallint NOT NULL,project_id uuid NOT NULL,position_id uuid NOT NULL,decision text NOT NULL DEFAULT 'PENDING' CHECK(decision IN ('PENDING','ACCEPTED','DECLINED','CANCELLED','REJECTED','REPLACED','COMPLETED')),terms_version bigint NOT NULL,terms_snapshot jsonb NOT NULL,terms_hash text NOT NULL,decision_at timestamptz,assignment_id uuid,source_version bigint NOT NULL DEFAULT 1,updated_at timestamptz NOT NULL DEFAULT clock_timestamp()) PARTITION BY LIST(owner_region);
CREATE TABLE global.assignments(owner_region smallint NOT NULL,assignment_id uuid NOT NULL DEFAULT gen_random_uuid(),employee_id uuid NOT NULL,invitation_id uuid NOT NULL,project_region smallint NOT NULL,project_id uuid NOT NULL,position_id uuid NOT NULL,terms_version bigint NOT NULL,date_from date NOT NULL,date_to date NOT NULL,hours_per_week numeric(4,1) NOT NULL CHECK(hours_per_week>0 AND hours_per_week<=40 AND mod(hours_per_week,0.5)=0),status text NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','CONFLICT','CANCELLED','COMPLETED')),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),CHECK(date_from<=date_to)) PARTITION BY LIST(owner_region);
CREATE TABLE global.workflow_events(owner_region smallint NOT NULL,event_id uuid NOT NULL DEFAULT gen_random_uuid(),object_type text NOT NULL,object_id uuid NOT NULL,object_version bigint NOT NULL DEFAULT 1,actor_id uuid,event_type text NOT NULL,occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),details jsonb NOT NULL DEFAULT '{}') PARTITION BY LIST(owner_region);
CREATE TABLE global.reviews(owner_region smallint NOT NULL,review_id uuid NOT NULL DEFAULT gen_random_uuid(),project_id uuid NOT NULL,employee_region smallint NOT NULL,employee_id uuid NOT NULL,assignment_id uuid NOT NULL,author_id uuid NOT NULL,rating integer NOT NULL CHECK(rating BETWEEN 1 AND 5),comment text NOT NULL CHECK(length(btrim(comment)) BETWEEN 1 AND 2000),version bigint NOT NULL DEFAULT 1,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp()) PARTITION BY LIST(owner_region);
CREATE TABLE global.processed_commands(owner_region smallint NOT NULL,command_id uuid NOT NULL,source_region smallint NOT NULL,actor_id uuid,kind text NOT NULL,payload_hash text NOT NULL,payload jsonb NOT NULL,result jsonb,processed_at timestamptz NOT NULL DEFAULT clock_timestamp()) PARTITION BY LIST(owner_region);

DO $$
DECLARE r integer; t text; pk text; own integer := private.node_region(); tables text[] := ARRAY['employees','employee_competencies','capacity_periods','unavailability_periods','projects','project_positions','position_competencies','invitations','invitation_responses','assignments','workflow_events','reviews','processed_commands'];
BEGIN
  FOREACH r IN ARRAY ARRAY[1,2] LOOP
    FOREACH t IN ARRAY tables LOOP
      EXECUTE format('CREATE TABLE r%s_data.%I (LIKE global.%I INCLUDING DEFAULTS INCLUDING CONSTRAINTS)',r,t,t);
      pk := CASE t WHEN 'employees' THEN 'employee_id' WHEN 'employee_competencies' THEN 'employee_id,competency_id' WHEN 'capacity_periods' THEN 'id' WHEN 'unavailability_periods' THEN 'id' WHEN 'projects' THEN 'project_id' WHEN 'project_positions' THEN 'position_id' WHEN 'position_competencies' THEN 'position_id,competency_id' WHEN 'invitations' THEN 'invitation_id' WHEN 'invitation_responses' THEN 'invitation_id' WHEN 'assignments' THEN 'assignment_id' WHEN 'workflow_events' THEN 'event_id' WHEN 'reviews' THEN 'review_id' ELSE 'command_id' END;
      EXECUTE format('ALTER TABLE r%s_data.%I ADD PRIMARY KEY(owner_region,%s), ADD CHECK(owner_region=%s)',r,t,pk,r);
      IF own=r THEN EXECUTE format('ALTER TABLE global.%I ATTACH PARTITION r%s_data.%I FOR VALUES IN (%s)',t,r,t,r); END IF;
    END LOOP;
    EXECUTE format('ALTER TABLE r%s_data.assignments ADD UNIQUE(owner_region,invitation_id)',r);
    EXECUTE format('ALTER TABLE r%s_data.reviews ADD UNIQUE(owner_region,employee_region,assignment_id,author_id)',r);
    EXECUTE format('CREATE INDEX ON r%s_data.assignments(employee_id,date_from,date_to) WHERE status IN (''ACTIVE'',''CONFLICT'')',r);
    EXECUTE format('CREATE INDEX ON r%s_data.workflow_events(object_type,object_id,occurred_at)',r);
    EXECUTE format('CREATE INDEX ON r%s_data.invitations(position_id,status)',r);
    IF own=r THEN
      FOREACH t IN ARRAY ARRAY['employee_competencies','capacity_periods','unavailability_periods','invitation_responses','assignments'] LOOP EXECUTE format('ALTER TABLE r%s_data.%I ADD FOREIGN KEY(owner_region,employee_id) REFERENCES r%s_data.employees(owner_region,employee_id)',r,t,r); END LOOP;
      FOREACH t IN ARRAY ARRAY['project_positions','reviews'] LOOP EXECUTE format('ALTER TABLE r%s_data.%I ADD FOREIGN KEY(owner_region,project_id) REFERENCES r%s_data.projects(owner_region,project_id)',r,t,r); END LOOP;
      FOREACH t IN ARRAY ARRAY['position_competencies','invitations'] LOOP EXECUTE format('ALTER TABLE r%s_data.%I ADD FOREIGN KEY(owner_region,position_id) REFERENCES r%s_data.project_positions(owner_region,position_id)',r,t,r); END LOOP;
      FOREACH t IN ARRAY ARRAY['employee_competencies','position_competencies'] LOOP EXECUTE format('ALTER TABLE r%s_data.%I ADD FOREIGN KEY(competency_id) REFERENCES cat.competencies(competency_id)',r,t); END LOOP;
      EXECUTE format('ALTER TABLE r%s_data.assignments ADD FOREIGN KEY(owner_region,invitation_id) REFERENCES r%s_data.invitation_responses(owner_region,invitation_id)',r,r);
      EXECUTE format('ALTER TABLE r%s_data.capacity_periods ADD EXCLUDE USING gist(employee_id WITH =,daterange(date_from,date_to,''[]'') WITH &&)',r);
      EXECUTE format('CREATE UNIQUE INDEX ON r%s_data.invitations(position_id) WHERE status=''PENDING''',r);
    END IF;
  END LOOP;
END $$;

CREATE TABLE private.employee_private(owner_region smallint NOT NULL,employee_id uuid NOT NULL,full_name text,contact_email text,contact_phone text,PRIMARY KEY(owner_region,employee_id),CHECK(owner_region=private.node_region()));
CREATE TABLE private.accounts(account_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),owner_region smallint NOT NULL DEFAULT private.node_region(),employee_id uuid,login text NOT NULL UNIQUE,password_hash text NOT NULL,active boolean NOT NULL DEFAULT true,CHECK(owner_region=private.node_region()));
CREATE TABLE private.account_roles(account_id uuid NOT NULL REFERENCES private.accounts(account_id),role_code text NOT NULL CHECK(role_code IN ('EMPLOYEE','EDITOR','MANAGER','CATALOG_ADMIN','ANALYST')),scope_id text NOT NULL DEFAULT '*',PRIMARY KEY(account_id,role_code,scope_id));
CREATE TABLE private.outbox(message_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),target_region smallint NOT NULL CHECK(target_region IN (1,2)),command_id uuid NOT NULL DEFAULT gen_random_uuid(),kind text NOT NULL,payload jsonb NOT NULL,actor_id uuid,state text NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','DELIVERED')),attempts integer NOT NULL DEFAULT 0,next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),delivered_at timestamptz,last_error text,UNIQUE(target_region,command_id));
CREATE INDEX ON private.outbox(next_attempt_at) WHERE state='PENDING';
DO $$ BEGIN IF private.node_region()=0 THEN
 CREATE TABLE private.processed_commands(LIKE global.processed_commands INCLUDING DEFAULTS INCLUDING CONSTRAINTS,PRIMARY KEY(owner_region,command_id),CHECK(owner_region=0));
 ALTER TABLE global.processed_commands ATTACH PARTITION private.processed_commands FOR VALUES IN(0);
 CREATE TABLE private.catalog_events(LIKE global.workflow_events INCLUDING DEFAULTS INCLUDING CONSTRAINTS,PRIMARY KEY(owner_region,event_id),CHECK(owner_region=0));
 ALTER TABLE global.workflow_events ATTACH PARTITION private.catalog_events FOR VALUES IN(0);
END IF; END $$;
DO $$ DECLARE n integer:=private.node_region(); BEGIN
 IF n IN (1,2) THEN
  EXECUTE format('ALTER TABLE private.employee_private ADD FOREIGN KEY(owner_region,employee_id) REFERENCES r%s_data.employees(owner_region,employee_id)',n);
  EXECUTE format('ALTER TABLE private.accounts ADD FOREIGN KEY(owner_region,employee_id) REFERENCES r%s_data.employees(owner_region,employee_id)',n);
 END IF;
END $$;

CREATE FUNCTION private.command_hash(p jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT encode(public.digest(p::text,'sha256'),'hex') $$;
CREATE FUNCTION private.actor_id() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.actor_id',true),'')::uuid $$;
CREATE FUNCTION private.has_role(role_name text,scope text DEFAULT '*') RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,private AS $$ SELECT EXISTS(SELECT FROM private.accounts a JOIN private.account_roles r USING(account_id) WHERE a.account_id=private.actor_id() AND a.active AND r.role_code=role_name AND (r.scope_id='*' OR r.scope_id=scope)) $$;
CREATE FUNCTION private.assert_role(role_name text,scope text DEFAULT '*') RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$ BEGIN IF NOT private.has_role(role_name,scope) THEN RAISE EXCEPTION 'Недостаточно прав: %',role_name USING ERRCODE='42501'; END IF; END $$;
CREATE FUNCTION private.immutable_owner() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.owner_region<>OLD.owner_region THEN RAISE EXCEPTION 'Владелец неизменяем' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
DO $$ DECLARE t text;n integer:=private.node_region(); BEGIN IF n IN(1,2) THEN FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='r'||n||'_data' LOOP EXECUTE format('CREATE TRIGGER immutable_owner BEFORE UPDATE ON r%s_data.%I FOR EACH ROW EXECUTE FUNCTION private.immutable_owner()',n,t); END LOOP; END IF; END $$;

GRANT USAGE ON SCHEMA global,cat,search,analytics,private TO matcher_app;
GRANT SELECT ON ALL TABLES IN SCHEMA global,cat TO matcher_app;
GRANT SELECT ON private.accounts,private.account_roles TO matcher_app;
ALTER TABLE private.employee_private ENABLE ROW LEVEL SECURITY;
CREATE POLICY private_profile_read ON private.employee_private FOR SELECT TO matcher_app USING(owner_region=private.node_region() AND (private.has_role('EDITOR',owner_region::text) OR employee_id=(SELECT a.employee_id FROM private.accounts a WHERE a.account_id=private.actor_id() AND a.active)));
GRANT SELECT ON private.employee_private TO matcher_app;
GRANT USAGE ON SCHEMA private,global TO matcher_worker;
GRANT SELECT,UPDATE ON private.outbox TO matcher_worker;
GRANT SELECT,INSERT ON global.processed_commands TO matcher_worker;
GRANT EXECUTE ON FUNCTION private.node_region(),private.actor_id(),private.has_role(text,text),private.command_hash(jsonb) TO matcher_app,matcher_worker;
REVOKE ALL ON FUNCTION private.assert_role(text,text) FROM PUBLIC;

DO $$ DECLARE n integer:=private.node_region(); names text; BEGIN
 IF n=0 THEN CREATE PUBLICATION cat_catalog FOR TABLE cat.regions,cat.competencies WITH(publish='insert,update,delete');
 ELSE SELECT string_agg(format('r%s_data.%I',n,tablename),',') INTO names FROM pg_tables WHERE schemaname='r'||n||'_data' AND tablename<>'processed_commands'; EXECUTE format('CREATE PUBLICATION r%s_working FOR TABLE %s WITH(publish=''insert,update,delete'')',n,names); END IF;
END $$;
