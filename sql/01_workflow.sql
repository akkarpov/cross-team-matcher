\set ON_ERROR_STOP on
-- Only local-owner relations are touched by command handlers. The owner predicate
-- is constant for this database, so foreign partitions are never consulted here.
CREATE FUNCTION private.event(object_kind text,object_uuid uuid,event_name text,event_details jsonb DEFAULT '{}',object_ver bigint DEFAULT 1,event_actor uuid DEFAULT private.actor_id()) RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,private AS $$
 INSERT INTO global.workflow_events(owner_region,object_type,object_id,object_version,actor_id,event_type,details) VALUES(private.node_region(),object_kind,object_uuid,object_ver,event_actor,event_name,event_details)
$$;
CREATE FUNCTION private.enqueue(target smallint,command_kind text,body jsonb,actor uuid DEFAULT private.actor_id()) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$ DECLARE id uuid; BEGIN INSERT INTO private.outbox(target_region,kind,payload,actor_id) VALUES(target,command_kind,body,actor) RETURNING message_id INTO id; RETURN id; END $$;
CREATE FUNCTION private.valid_dates(first_day date,last_day date) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT first_day<=last_day AND last_day-first_day<=3660 AND EXISTS(SELECT FROM generate_series(first_day::timestamp,last_day::timestamp,interval '1 day') d WHERE extract(isodow FROM d)<6) $$;
CREATE FUNCTION private.guard(employee uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$ BEGIN
 UPDATE global.employees SET schedule_version=schedule_version+1,updated_at=clock_timestamp() WHERE owner_region=private.node_region() AND employee_id=employee;
 IF NOT FOUND THEN RAISE EXCEPTION 'Сотрудник не найден' USING ERRCODE='P0002'; END IF;
END $$;
-- The receiver executes no remote lookup: source identity is authenticated by
-- postgres_fdw's dedicated transport login, never a payload field.
CREATE FUNCTION private.receive(kind text,p jsonb,source smallint,actor uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$
DECLARE n smallint:=private.node_region(); r global.invitation_responses; i global.invitations; a global.assignments; project uuid; employee uuid:=(p->>'employee_id')::uuid; inv_id uuid:=(p->>'invitation_id')::uuid; terminal text;
BEGIN
 IF kind NOT IN('OFFER','CANCEL','COMPLETE','RESULT') OR source NOT IN(1,2) OR n NOT IN(1,2) THEN RAISE EXCEPTION 'Тип служебной команды запрещен' USING ERRCODE='42501'; END IF;
 IF kind='RESULT' THEN
  SELECT * INTO i FROM global.invitations WHERE owner_region=n AND invitation_id=inv_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Исходная оферта еще не доступна' USING ERRCODE='55000'; END IF;
  IF i.employee_region<>source OR i.employee_id<>employee OR i.terms_version<>(p->>'terms_version')::bigint OR i.terms_hash<>p->>'terms_hash' THEN RAISE EXCEPTION 'Несовпадение источника или версии ответа' USING ERRCODE='42501'; END IF;
  IF (p->>'source_version')::bigint<=i.version THEN RETURN jsonb_build_object('ok',true,'stale',true); END IF;
  IF i.status IN('CANCELLED','COMPLETED','DECLINED','REJECTED','REPLACED') AND p->>'decision' NOT IN('CANCELLED','COMPLETED','REPLACED') THEN RETURN jsonb_build_object('ok',true,'terminal',true); END IF;
  IF p->>'decision' NOT IN('PENDING','ACCEPTED','DECLINED','CANCELLED','REJECTED','REPLACED','COMPLETED') THEN RAISE EXCEPTION 'Некорректный результат' USING ERRCODE='23514'; END IF;
  UPDATE global.invitations SET status=CASE WHEN i.status='CANCELLING' AND p->>'decision' IN('PENDING','ACCEPTED') THEN 'CANCELLING' ELSE p->>'decision' END,assignment_id=(p->>'assignment_id')::uuid,assignment_status=p->>'assignment_status',version=(p->>'source_version')::bigint,updated_at=clock_timestamp() WHERE owner_region=n AND invitation_id=inv_id;
  SELECT project_id INTO project FROM global.project_positions WHERE owner_region=n AND position_id=i.position_id;
  PERFORM private.event('invitation',inv_id,'INVITATION.RESULT',p,(p->>'source_version')::bigint,actor);
  PERFORM private.refresh_project(project);
  RETURN jsonb_build_object('ok',true,'invitation_id',inv_id);
 END IF;
 IF (p->>'terms_hash') IS NULL OR private.command_hash(p->'terms_snapshot')<>p->>'terms_hash' OR (p->'terms_snapshot'->>'project_id')::uuid<>(p->>'project_id')::uuid OR (p->'terms_snapshot'->>'position_id')::uuid<>(p->>'position_id')::uuid THEN RAISE EXCEPTION 'Поврежден снимок условий' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT FROM global.employees WHERE owner_region=n AND employee_id=employee) THEN RAISE EXCEPTION 'Сотрудник еще не доступен на владельце' USING ERRCODE='55000'; END IF;
 PERFORM private.guard(employee);
 SELECT * INTO r FROM global.invitation_responses WHERE owner_region=n AND invitation_id=inv_id FOR UPDATE;
 IF FOUND THEN
  IF r.employee_id<>employee OR r.project_region<>source OR r.terms_version<>(p->>'terms_version')::bigint OR r.terms_hash<>p->>'terms_hash' THEN RAISE EXCEPTION 'Повтор оферты изменяет неизменяемые условия' USING ERRCODE='23514'; END IF;
 ELSE
  IF kind='COMPLETE' THEN RAISE EXCEPTION 'Оферта еще не доставлена' USING ERRCODE='55000'; END IF;
  terminal:=CASE WHEN kind='CANCEL' THEN 'CANCELLED' WHEN NOT EXISTS(SELECT FROM global.employees WHERE owner_region=n AND employee_id=employee AND active) THEN 'REJECTED' ELSE 'PENDING' END;
  INSERT INTO global.invitation_responses(owner_region,invitation_id,employee_id,project_region,project_id,position_id,decision,terms_version,terms_snapshot,terms_hash,source_version) VALUES(n,inv_id,employee,source,(p->>'project_id')::uuid,(p->>'position_id')::uuid,terminal,(p->>'terms_version')::bigint,p->'terms_snapshot',p->>'terms_hash',2) RETURNING * INTO r;
  PERFORM private.event('invitation',inv_id,'INVITATION.'||CASE WHEN kind='CANCEL' THEN 'CANCELLED_BEFORE_OFFER' ELSE 'RECEIVED' END,jsonb_build_object('project_region',source,'terms_version',r.terms_version),r.source_version,actor);
 END IF;
 IF kind='OFFER' THEN
  PERFORM private.enqueue(source,'RESULT',private.response_result(inv_id),actor);
  RETURN jsonb_build_object('ok',true,'decision',r.decision);
 END IF;
 SELECT * INTO a FROM global.assignments WHERE owner_region=n AND invitation_id=inv_id;
 IF kind='COMPLETE' THEN
  IF r.decision='PENDING' THEN RAISE EXCEPTION 'Согласие еще не подтверждено' USING ERRCODE='55000'; END IF;
  IF r.decision NOT IN('ACCEPTED','COMPLETED') OR a.assignment_id IS NULL OR a.status NOT IN('ACTIVE','CONFLICT','COMPLETED') THEN RAISE EXCEPTION 'Нельзя завершить непринятое или отмененное участие' USING ERRCODE='23514'; END IF;
  terminal:='COMPLETED';
 ELSE terminal:='CANCELLED'; END IF;
 -- Completed and replaced work is historical; a late cancellation cannot erase it.
 IF r.decision NOT IN('COMPLETED','REPLACED','DECLINED','REJECTED') THEN
  UPDATE global.assignments SET status=terminal,updated_at=clock_timestamp() WHERE owner_region=n AND invitation_id=inv_id AND status IN('ACTIVE','CONFLICT');
  UPDATE global.invitation_responses SET decision=terminal,decision_at=clock_timestamp(),source_version=source_version+1,updated_at=clock_timestamp() WHERE owner_region=n AND invitation_id=inv_id AND decision<>terminal;
  PERFORM private.event('invitation',inv_id,'INVITATION.'||terminal,jsonb_build_object('assignment_id',a.assignment_id),r.source_version+1,actor);
 END IF;
 PERFORM private.enqueue(source,'RESULT',private.response_result(inv_id),actor);
 RETURN jsonb_build_object('ok',true,'invitation_id',inv_id,'decision',terminal);
END $$;

CREATE FUNCTION private.process_command() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$
DECLARE result_json jsonb; source_login integer; prior_actor text;
BEGIN
 IF NEW.owner_region<>private.node_region() OR NEW.payload_hash<>private.command_hash(NEW.payload) THEN RAISE EXCEPTION 'Некорректная квитанция' USING ERRCODE='23514'; END IF;
 IF NEW.kind LIKE 'LOCAL.%' THEN
  IF session_user NOT IN('matcher_app','postgres','matcher_owner') OR NEW.source_region<>private.node_region() OR NEW.actor_id IS DISTINCT FROM private.actor_id() THEN RAISE EXCEPTION 'Удаленное согласие и подмена пользователя запрещены' USING ERRCODE='42501'; END IF;
  result_json:=private.dispatch(substr(NEW.kind,7),NEW.payload);
 ELSE
  source_login:=CASE session_user WHEN 'transport_r1' THEN 1 WHEN 'transport_r2' THEN 2 WHEN 'transport_c' THEN 0 WHEN 'matcher_worker' THEN private.node_region() ELSE -1 END;
  IF source_login<>NEW.source_region THEN RAISE EXCEPTION 'Источник команды не соответствует доверенной роли' USING ERRCODE='42501'; END IF;
  result_json:=private.receive(NEW.kind,NEW.payload,NEW.source_region,NEW.actor_id);
 END IF;
 UPDATE global.processed_commands SET result=result_json,processed_at=clock_timestamp() WHERE owner_region=NEW.owner_region AND command_id=NEW.command_id;
 RETURN NULL;
END $$;
DO $$ DECLARE n smallint:=private.node_region(); target text; BEGIN
 target:=CASE WHEN n=0 THEN 'private.processed_commands' ELSE format('r%s_data.processed_commands',n) END;
 EXECUTE format('CREATE TRIGGER apply_command AFTER INSERT ON %s FOR EACH ROW EXECUTE FUNCTION private.process_command()',target);
END $$;

CREATE FUNCTION global.mutate(action text,payload jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$
DECLARE n smallint:=private.node_region(); id uuid:=COALESCE((payload->>'command_id')::uuid,gen_random_uuid()); body jsonb:=payload-'command_id'; existing global.processed_commands;
BEGIN
 IF private.actor_id() IS NULL OR NOT EXISTS(SELECT FROM private.accounts WHERE account_id=private.actor_id() AND active) THEN RAISE EXCEPTION 'Учетная запись неактивна' USING ERRCODE='42501'; END IF;
 SELECT * INTO existing FROM global.processed_commands WHERE owner_region=n AND command_id=id;
 IF NOT FOUND THEN
  BEGIN
   INSERT INTO global.processed_commands(owner_region,command_id,source_region,actor_id,kind,payload_hash,payload) VALUES(n,id,n,private.actor_id(),'LOCAL.'||action,private.command_hash(body),body);
  EXCEPTION WHEN unique_violation THEN
   SELECT * INTO existing FROM global.processed_commands WHERE owner_region=n AND command_id=id;
   IF NOT FOUND THEN RAISE; END IF;
  END;
  SELECT * INTO existing FROM global.processed_commands WHERE owner_region=n AND command_id=id;
 END IF;
 IF existing.command_id IS NULL THEN RAISE EXCEPTION 'Не удалось записать команду' USING ERRCODE='40001'; END IF;
 IF existing.source_region<>n OR existing.kind<>'LOCAL.'||action OR existing.actor_id IS DISTINCT FROM private.actor_id() OR existing.payload_hash<>private.command_hash(body) THEN RAISE EXCEPTION 'command_id уже использован для другого действия' USING ERRCODE='23514'; END IF;
 RETURN existing.result||jsonb_build_object('command_id',id);
END $$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA private FROM PUBLIC;
REVOKE ALL ON FUNCTION global.mutate(text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION global.mutate(text,jsonb) TO matcher_app;
GRANT EXECUTE ON FUNCTION private.node_region(),private.actor_id(),private.has_role(text,text),private.command_hash(jsonb) TO matcher_app,matcher_worker;
GRANT USAGE ON SCHEMA r1_data,r2_data TO transport_c,transport_r1,transport_r2;
GRANT SELECT ON ALL TABLES IN SCHEMA r1_data,r2_data TO transport_c,transport_r1,transport_r2;
GRANT INSERT ON r1_data.processed_commands,r2_data.processed_commands TO transport_c,transport_r1,transport_r2;
-- Replicas receive only logical apply writes; transport INSERT is granted only
-- on the actual primary receipt table, otherwise a local copy could be modified.
DO $$ BEGIN
 IF private.node_region()<>1 THEN REVOKE INSERT ON r1_data.processed_commands FROM transport_c,transport_r1,transport_r2; END IF;
 IF private.node_region()<>2 THEN REVOKE INSERT ON r2_data.processed_commands FROM transport_c,transport_r1,transport_r2; END IF;
END $$;
CREATE FUNCTION private.calendar_fits(employee uuid,first_day date,last_day date,requested numeric,exclude_invitation uuid DEFAULT NULL) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,private AS $$
 SELECT private.valid_dates(first_day,last_day) AND NOT EXISTS(
 SELECT FROM generate_series(first_day::timestamp,last_day::timestamp,interval '1 day') day
 WHERE extract(isodow FROM day)<6 AND (
 COALESCE((SELECT c.hours_per_week/5 FROM global.capacity_periods c WHERE c.owner_region=private.node_region() AND c.employee_id=employee AND day::date BETWEEN c.date_from AND c.date_to),0)
 * CASE WHEN EXISTS(SELECT FROM global.unavailability_periods u WHERE u.owner_region=private.node_region() AND u.employee_id=employee AND u.active AND day::date BETWEEN u.date_from AND u.date_to) THEN 0 ELSE 1 END
 < requested/5 + COALESCE((SELECT sum(a.hours_per_week/5) FROM global.assignments a WHERE a.owner_region=private.node_region() AND a.employee_id=employee AND a.status IN('ACTIVE','CONFLICT') AND (exclude_invitation IS NULL OR a.invitation_id<>exclude_invitation) AND day::date BETWEEN a.date_from AND a.date_to),0)))
$$;
CREATE FUNCTION private.requirements_fit(employee uuid,terms jsonb) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,private AS $$
 SELECT EXISTS(SELECT FROM global.employees e WHERE e.owner_region=private.node_region() AND e.employee_id=employee AND e.active AND e.experience_months>=(terms->>'min_experience')::integer)
 AND NOT EXISTS(SELECT FROM jsonb_array_elements_text(terms->'competency_ids') c(id) WHERE NOT EXISTS(SELECT FROM global.employee_competencies ec JOIN cat.competencies cat ON cat.competency_id=ec.competency_id AND cat.active WHERE ec.owner_region=private.node_region() AND ec.employee_id=employee AND ec.competency_id=c.id::uuid))
$$;
CREATE FUNCTION private.assignment_conflicts(employee uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$ DECLARE a record; next_status text; BEGIN
 FOR a IN SELECT * FROM global.assignments WHERE owner_region=private.node_region() AND employee_id=employee AND status IN('ACTIVE','CONFLICT') LOOP
 next_status:=CASE WHEN private.calendar_fits(employee,a.date_from,a.date_to,0) THEN 'ACTIVE' ELSE 'CONFLICT' END;
 IF a.status<>next_status THEN
  UPDATE global.assignments SET status=next_status,updated_at=clock_timestamp() WHERE owner_region=private.node_region() AND assignment_id=a.assignment_id;
  UPDATE global.invitation_responses SET source_version=source_version+1,updated_at=clock_timestamp() WHERE owner_region=private.node_region() AND invitation_id=a.invitation_id;
  PERFORM private.event('assignment',a.assignment_id,'ASSIGNMENT.'||next_status,jsonb_build_object('invitation_id',a.invitation_id));
  PERFORM private.enqueue(a.project_region,'RESULT',private.response_result(a.invitation_id));
 END IF; END LOOP;
END $$;
CREATE FUNCTION private.manager_project(project uuid) RETURNS global.projects LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$ DECLARE p global.projects; BEGIN
 PERFORM private.assert_role('MANAGER',project::text);
 SELECT * INTO p FROM global.projects WHERE owner_region=private.node_region() AND project_id=project FOR UPDATE;
 IF NOT FOUND OR p.manager_id<>private.actor_id() THEN RAISE EXCEPTION 'Проект недоступен менеджеру' USING ERRCODE='42501'; END IF;
 RETURN p;
END $$;
CREATE FUNCTION private.response_result(invitation uuid) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,private AS $$
 SELECT jsonb_build_object('invitation_id',r.invitation_id,'employee_id',r.employee_id,'project_id',r.project_id,'position_id',r.position_id,'decision',r.decision,'terms_version',r.terms_version,'terms_hash',r.terms_hash,'source_version',r.source_version,'assignment_id',r.assignment_id,'assignment_status',a.status)
 FROM global.invitation_responses r LEFT JOIN global.assignments a ON a.owner_region=private.node_region() AND a.assignment_id=r.assignment_id WHERE r.owner_region=private.node_region() AND r.invitation_id=invitation
$$;
CREATE FUNCTION private.refresh_project(project uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$ DECLARE p global.projects; ready boolean; remaining boolean; BEGIN
 SELECT * INTO p FROM global.projects WHERE owner_region=private.node_region() AND project_id=project FOR UPDATE;
 SELECT EXISTS(SELECT FROM global.project_positions WHERE owner_region=private.node_region() AND project_id=project) AND NOT EXISTS(SELECT FROM global.project_positions pos WHERE pos.owner_region=private.node_region() AND pos.project_id=project AND NOT EXISTS(SELECT FROM global.invitations i WHERE i.owner_region=private.node_region() AND i.position_id=pos.position_id AND i.status IN('ACCEPTED','COMPLETED') AND i.assignment_status IN('ACTIVE','COMPLETED'))) INTO ready;
 IF p.status IN('FORMING','READY') THEN UPDATE global.projects SET status=CASE WHEN ready THEN 'READY' ELSE 'FORMING' END,version=version+1,updated_at=clock_timestamp() WHERE owner_region=private.node_region() AND project_id=project AND status<>CASE WHEN ready THEN 'READY' ELSE 'FORMING' END;
 ELSIF p.status='CANCELLING' THEN
 SELECT EXISTS(SELECT FROM global.invitations i JOIN global.project_positions pos ON pos.owner_region=private.node_region() AND pos.position_id=i.position_id WHERE i.owner_region=private.node_region() AND pos.project_id=project AND i.status IN('PENDING','ACCEPTED','CANCELLING')) INTO remaining;
 IF NOT remaining THEN UPDATE global.projects SET status='CANCELLED',version=version+1,updated_at=clock_timestamp() WHERE owner_region=private.node_region() AND project_id=project; PERFORM private.event('project',project,'PROJECT.CANCELLED'); END IF;
 END IF;
END $$;

CREATE FUNCTION private.offer(position_uuid uuid,employee_region smallint,employee uuid,invitation uuid DEFAULT gen_random_uuid(),replacing uuid DEFAULT NULL,overrides jsonb DEFAULT '{}') RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$
DECLARE pos global.project_positions; proj global.projects; previous global.invitations; terms jsonb; ver bigint:=1; hash text;
BEGIN
 SELECT * INTO pos FROM global.project_positions WHERE owner_region=private.node_region() AND position_id=position_uuid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Позиция не найдена' USING ERRCODE='P0002'; END IF;
 proj:=private.manager_project(pos.project_id);
 IF proj.status NOT IN('FORMING','READY','ACTIVE') THEN RAISE EXCEPTION 'Проект закрыт для приглашений' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT FROM global.invitations WHERE owner_region=private.node_region() AND position_id=position_uuid AND status IN('PENDING','CANCELLING')) THEN RAISE EXCEPTION 'На позицию уже отправлена открытая оферта' USING ERRCODE='23514'; END IF;
 IF replacing IS NULL AND EXISTS(SELECT FROM global.invitations WHERE owner_region=private.node_region() AND position_id=position_uuid AND status IN('ACCEPTED','COMPLETED')) THEN RAISE EXCEPTION 'Позиция уже занята' USING ERRCODE='23514'; END IF;
 IF replacing IS NOT NULL THEN
  SELECT * INTO previous FROM global.invitations WHERE owner_region=private.node_region() AND invitation_id=replacing AND status='ACCEPTED';
  IF NOT FOUND OR previous.employee_id<>employee OR previous.employee_region<>employee_region THEN RAISE EXCEPTION 'Исходное назначение недоступно для замены' USING ERRCODE='23514'; END IF;
  ver:=previous.terms_version+1;
 END IF;
 terms:=jsonb_build_object('project_id',proj.project_id,'project_name',proj.name,'position_id',position_uuid,'position_title',pos.title,'date_from',proj.date_from,'date_to',proj.date_to,'hours_per_week',pos.hours_per_week,'min_experience',pos.min_experience,'competency_ids',COALESCE((SELECT jsonb_agg(competency_id ORDER BY competency_id) FROM global.position_competencies WHERE owner_region=private.node_region() AND position_id=position_uuid),'[]'::jsonb),'replaces_invitation_id',replacing);
 terms:=terms||(SELECT COALESCE(jsonb_object_agg(key,value),'{}') FROM jsonb_each(overrides) WHERE key IN('date_from','date_to','hours_per_week','min_experience','competency_ids'));
 IF NOT private.valid_dates((terms->>'date_from')::date,(terms->>'date_to')::date) OR (terms->>'hours_per_week')::numeric NOT BETWEEN 0.5 AND 40 OR mod((terms->>'hours_per_week')::numeric,0.5)<>0 OR (terms->>'min_experience')::integer<0 THEN RAISE EXCEPTION 'Некорректные условия оферты' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT FROM jsonb_array_elements_text(terms->'competency_ids') x(id) WHERE NOT EXISTS(SELECT FROM cat.competencies WHERE competency_id=x.id::uuid AND active)) THEN RAISE EXCEPTION 'Неизвестная или неактивная компетенция' USING ERRCODE='23514'; END IF;
 -- Local original or local replica only: a stale positive is rechecked at acceptance.
 IF NOT EXISTS(SELECT FROM search.employee_profiles WHERE owner_region=employee_region AND employee_id=employee AND active) THEN RAISE EXCEPTION 'Активный сотрудник не найден в рабочем каталоге' USING ERRCODE='23514'; END IF;
 hash:=private.command_hash(terms);
 INSERT INTO global.invitations(owner_region,invitation_id,position_id,employee_region,employee_id,terms_version,terms_snapshot,terms_hash,replaces_invitation_id) VALUES(private.node_region(),invitation,position_uuid,employee_region,employee,ver,terms,hash,replacing);
 PERFORM private.event('invitation',invitation,'INVITATION.CREATED',jsonb_build_object('terms_version',ver,'terms_hash',hash,'employee_region',employee_region,'employee_id',employee));
 PERFORM private.enqueue(employee_region,'OFFER',jsonb_build_object('invitation_id',invitation,'employee_id',employee,'project_id',proj.project_id,'position_id',position_uuid,'terms_version',ver,'terms_snapshot',terms,'terms_hash',hash));
 RETURN jsonb_build_object('ok',true,'invitation_id',invitation,'terms_version',ver);
END $$;

CREATE FUNCTION private.dispatch(action text,p jsonb) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,private AS $$
#variable_conflict use_variable
DECLARE n smallint:=private.node_region(); actor uuid:=private.actor_id(); id uuid; employee uuid; project uuid; row_json jsonb; old_json jsonb; item jsonb; proj global.projects; pos global.project_positions; inv global.invitations; resp global.invitation_responses; rev global.reviews; a global.assignments; old_a global.assignments; accepted boolean; role_rec record; replacement uuid;
BEGIN
 IF actor IS NULL OR NOT EXISTS(SELECT FROM private.accounts WHERE account_id=actor AND active) THEN RAISE EXCEPTION 'Вход не выполнен' USING ERRCODE='42501'; END IF;
 IF action LIKE 'employee.%' OR action LIKE 'calendar.%' THEN
  PERFORM private.assert_role('EDITOR',n::text); IF n=0 THEN RAISE EXCEPTION 'Редактирование сотрудников доступно в регионе' USING ERRCODE='23514'; END IF;
  employee:=COALESCE((p->>'employee_id')::uuid,gen_random_uuid());
  IF action='employee.create' THEN
   INSERT INTO global.employees(owner_region,employee_id,nickname,experience_months,active) VALUES(n,employee,btrim(p->>'nickname'),COALESCE((p->>'experience_months')::integer,0),COALESCE((p->>'active')::boolean,true));
   INSERT INTO private.employee_private(owner_region,employee_id,full_name,contact_email,contact_phone) VALUES(n,employee,p->>'full_name',p->>'contact_email',p->>'contact_phone');
  ELSE
   PERFORM private.guard(employee);
   IF action='employee.update' THEN
    UPDATE global.employees SET nickname=COALESCE(btrim(p->>'nickname'),nickname),experience_months=COALESCE((p->>'experience_months')::integer,experience_months),active=COALESCE((p->>'active')::boolean,active),updated_at=clock_timestamp() WHERE owner_region=n AND employee_id=employee;
    UPDATE private.employee_private SET full_name=CASE WHEN p?'full_name' THEN p->>'full_name' ELSE full_name END,contact_email=CASE WHEN p?'contact_email' THEN p->>'contact_email' ELSE contact_email END,contact_phone=CASE WHEN p?'contact_phone' THEN p->>'contact_phone' ELSE contact_phone END WHERE owner_region=n AND employee_id=employee;
   ELSIF action='employee.skills' THEN
    IF EXISTS(SELECT FROM jsonb_array_elements_text(p->'competency_ids') x(id) WHERE NOT EXISTS(SELECT FROM cat.competencies WHERE competency_id=x.id::uuid AND active)) THEN RAISE EXCEPTION 'Компетенция неактивна или отсутствует' USING ERRCODE='23514'; END IF;
    DELETE FROM global.employee_competencies WHERE owner_region=n AND employee_id=employee;
    INSERT INTO global.employee_competencies(owner_region,employee_id,competency_id) SELECT n,employee,value::uuid FROM jsonb_array_elements_text(p->'competency_ids');
   ELSIF action IN('calendar.capacity','calendar.unavailability') THEN
    IF NOT private.valid_dates((p->>'date_from')::date,(p->>'date_to')::date) THEN RAISE EXCEPTION 'Период должен содержать рабочий день и корректные границы' USING ERRCODE='23514'; END IF;
    id:=COALESCE((p->>'id')::uuid,gen_random_uuid());
    IF action='calendar.capacity' THEN
     DELETE FROM global.capacity_periods WHERE owner_region=n AND employee_id=employee AND capacity_periods.id=id;
     INSERT INTO global.capacity_periods(owner_region,id,employee_id,date_from,date_to,hours_per_week) VALUES(n,id,employee,(p->>'date_from')::date,(p->>'date_to')::date,(p->>'hours_per_week')::numeric);
    ELSE
     DELETE FROM global.unavailability_periods WHERE owner_region=n AND employee_id=employee AND unavailability_periods.id=id;
     INSERT INTO global.unavailability_periods(owner_region,id,employee_id,date_from,date_to) VALUES(n,id,employee,(p->>'date_from')::date,(p->>'date_to')::date);
    END IF;
    PERFORM private.assignment_conflicts(employee);
   ELSIF action='calendar.remove' THEN
    id:=(p->>'id')::uuid;
    IF p->>'kind'='CAPACITY' THEN DELETE FROM global.capacity_periods WHERE owner_region=n AND employee_id=employee AND capacity_periods.id=id;
    ELSIF p->>'kind'='UNAVAILABILITY' THEN UPDATE global.unavailability_periods SET active=false WHERE owner_region=n AND employee_id=employee AND unavailability_periods.id=id;
    ELSE RAISE EXCEPTION 'Неизвестный тип календарного периода' USING ERRCODE='23514'; END IF;
    IF NOT FOUND THEN RAISE EXCEPTION 'Период не найден' USING ERRCODE='P0002'; END IF;
    PERFORM private.assignment_conflicts(employee);
   ELSE RAISE EXCEPTION 'Неизвестная операция'; END IF;
  END IF;
  PERFORM private.event('employee',employee,upper(action),jsonb_build_object('calendar_id',id));
  RETURN jsonb_build_object('ok',true,'employee_id',employee,'id',id);
 ELSIF action='project.create' THEN
  PERFORM private.assert_role('MANAGER'); IF n=0 THEN RAISE EXCEPTION 'Проект создается в регионе' USING ERRCODE='23514'; END IF;
  IF NOT private.valid_dates((p->>'date_from')::date,(p->>'date_to')::date) THEN RAISE EXCEPTION 'Некорректный период проекта' USING ERRCODE='23514'; END IF;
  id:=COALESCE((p->>'project_id')::uuid,gen_random_uuid());
  INSERT INTO global.projects(owner_region,project_id,manager_id,name,description,date_from,date_to) VALUES(n,id,actor,btrim(p->>'name'),COALESCE(p->>'description',''),(p->>'date_from')::date,(p->>'date_to')::date);
  PERFORM private.event('project',id,'PROJECT.CREATED'); RETURN jsonb_build_object('ok',true,'project_id',id);
 ELSIF action IN('project.update','project.transition') THEN
  id:=(p->>'project_id')::uuid; proj:=private.manager_project(id);
  IF proj.version<>COALESCE((p->>'version')::bigint,-1) THEN RAISE EXCEPTION 'Проект изменен, обновите страницу' USING ERRCODE='40001'; END IF;
  IF action='project.update' THEN
   IF proj.status NOT IN('FORMING','READY') THEN RAISE EXCEPTION 'Изменение закрытого или активного проекта запрещено' USING ERRCODE='23514'; END IF;
   IF (p?'date_from' OR p?'date_to') AND EXISTS(SELECT FROM global.invitations i JOIN global.project_positions pos ON pos.owner_region=n AND pos.position_id=i.position_id WHERE i.owner_region=n AND pos.project_id=id) THEN RAISE EXCEPTION 'Даты оферты меняются через новую версию условий' USING ERRCODE='23514'; END IF;
   IF NOT private.valid_dates(COALESCE((p->>'date_from')::date,proj.date_from),COALESCE((p->>'date_to')::date,proj.date_to)) THEN RAISE EXCEPTION 'Некорректный период' USING ERRCODE='23514'; END IF;
   UPDATE global.projects SET name=COALESCE(btrim(p->>'name'),name),description=COALESCE(p->>'description',description),date_from=COALESCE((p->>'date_from')::date,date_from),date_to=COALESCE((p->>'date_to')::date,date_to),version=version+1,updated_at=clock_timestamp() WHERE owner_region=n AND project_id=id;
  ELSIF p->>'status'='ACTIVE' AND proj.status='READY' THEN UPDATE global.projects SET status='ACTIVE',version=version+1,updated_at=clock_timestamp() WHERE owner_region=n AND project_id=id;
  ELSIF p->>'status'='COMPLETED' AND proj.status IN('ACTIVE','READY') THEN
   IF EXISTS(SELECT FROM global.invitations i JOIN global.project_positions pos ON pos.owner_region=n AND pos.position_id=i.position_id WHERE i.owner_region=n AND pos.project_id=id AND i.status IN('PENDING','ACCEPTED','CANCELLING')) THEN RAISE EXCEPTION 'Сначала завершите участие всех сотрудников и дождитесь подтверждений' USING ERRCODE='23514'; END IF;
   UPDATE global.projects SET status='COMPLETED',version=version+1,updated_at=clock_timestamp() WHERE owner_region=n AND project_id=id;
  ELSIF p->>'status'='CANCELLED' AND proj.status IN('FORMING','READY','ACTIVE') THEN
   UPDATE global.projects SET status='CANCELLING',version=version+1,updated_at=clock_timestamp() WHERE owner_region=n AND project_id=id;
   FOR inv IN SELECT i.* FROM global.invitations i JOIN global.project_positions pos ON pos.owner_region=n AND pos.position_id=i.position_id WHERE i.owner_region=n AND pos.project_id=id AND i.status IN('PENDING','ACCEPTED') LOOP
    UPDATE global.invitations SET status='CANCELLING',updated_at=clock_timestamp() WHERE owner_region=n AND invitation_id=inv.invitation_id;
    PERFORM private.enqueue(inv.employee_region,'CANCEL',jsonb_build_object('invitation_id',inv.invitation_id,'employee_id',inv.employee_id,'project_id',id,'position_id',inv.position_id,'terms_version',inv.terms_version,'terms_hash',inv.terms_hash,'terms_snapshot',inv.terms_snapshot));
   END LOOP; PERFORM private.refresh_project(id);
  ELSE RAISE EXCEPTION 'Недопустимый переход состояния проекта' USING ERRCODE='23514'; END IF;
  PERFORM private.event('project',id,upper(action),jsonb_build_object('previous_status',proj.status,'requested_status',p->>'status'),proj.version+1);
  RETURN jsonb_build_object('ok',true,'project_id',id);
 ELSIF action IN('position.create','position.update') THEN
  IF action='position.create' THEN project:=(p->>'project_id')::uuid; id:=COALESCE((p->>'position_id')::uuid,gen_random_uuid());
  ELSE id:=(p->>'position_id')::uuid; SELECT * INTO pos FROM global.project_positions WHERE owner_region=n AND position_id=id; project:=pos.project_id; END IF;
  proj:=private.manager_project(project);
  IF proj.status NOT IN('FORMING','READY','ACTIVE') THEN RAISE EXCEPTION 'Проект закрыт' USING ERRCODE='23514'; END IF;
  IF action='position.create' THEN
   IF (SELECT count(*) FROM global.project_positions WHERE owner_region=n AND project_id=project)>=10 THEN RAISE EXCEPTION 'Не более 10 позиций' USING ERRCODE='23514'; END IF;
   INSERT INTO global.project_positions(owner_region,position_id,project_id,title,min_experience,hours_per_week,search_mode) VALUES(n,id,project,btrim(p->>'title'),COALESCE((p->>'min_experience')::integer,0),(p->>'hours_per_week')::numeric,COALESCE(p->>'search_mode','OWN_FIRST'));
  ELSE
   UPDATE global.project_positions SET title=COALESCE(btrim(p->>'title'),title),min_experience=COALESCE((p->>'min_experience')::integer,min_experience),hours_per_week=COALESCE((p->>'hours_per_week')::numeric,hours_per_week),search_mode=COALESCE(p->>'search_mode',search_mode),version=version+1,updated_at=clock_timestamp() WHERE owner_region=n AND position_id=id;
  END IF;
  IF p?'competency_ids' THEN
   IF EXISTS(SELECT FROM jsonb_array_elements_text(p->'competency_ids') x(id) WHERE NOT EXISTS(SELECT FROM cat.competencies WHERE competency_id=x.id::uuid AND active)) THEN RAISE EXCEPTION 'Компетенция неактивна или отсутствует' USING ERRCODE='23514'; END IF;
   DELETE FROM global.position_competencies WHERE owner_region=n AND position_id=id;
   INSERT INTO global.position_competencies(owner_region,position_id,competency_id) SELECT n,id,value::uuid FROM jsonb_array_elements_text(p->'competency_ids');
  END IF;
  PERFORM private.event('position',id,upper(action)); PERFORM private.refresh_project(project); RETURN jsonb_build_object('ok',true,'position_id',id,'project_id',project);
 ELSIF action='invitation.create' THEN
  RETURN private.offer((p->>'position_id')::uuid,(p->>'employee_region')::smallint,(p->>'employee_id')::uuid,COALESCE((p->>'invitation_id')::uuid,gen_random_uuid()));
 ELSIF action='terms.replace' THEN
  SELECT * INTO inv FROM global.invitations WHERE owner_region=n AND invitation_id=(p->>'invitation_id')::uuid;
  IF NOT FOUND THEN RAISE EXCEPTION 'Приглашение не найдено' USING ERRCODE='P0002'; END IF;
  RETURN private.offer(inv.position_id,inv.employee_region,inv.employee_id,gen_random_uuid(),inv.invitation_id,p);
 ELSIF action IN('invitation.cancel','assignment.cancel','assignment.complete') THEN
  SELECT * INTO inv FROM global.invitations WHERE owner_region=n AND invitation_id=(p->>'invitation_id')::uuid;
  IF NOT FOUND THEN RAISE EXCEPTION 'Приглашение не найдено' USING ERRCODE='P0002'; END IF;
  SELECT project_id INTO project FROM global.project_positions WHERE owner_region=n AND position_id=inv.position_id; proj:=private.manager_project(project);
  IF inv.status NOT IN('PENDING','ACCEPTED') OR (action='assignment.complete' AND inv.status<>'ACCEPTED') THEN RAISE EXCEPTION 'Действие недоступно для состояния приглашения' USING ERRCODE='23514'; END IF;
  IF action<>'assignment.complete' THEN UPDATE global.invitations SET status='CANCELLING',updated_at=clock_timestamp() WHERE owner_region=n AND invitation_id=inv.invitation_id; END IF;
  PERFORM private.enqueue(inv.employee_region,CASE WHEN action='assignment.complete' THEN 'COMPLETE' ELSE 'CANCEL' END,jsonb_build_object('invitation_id',inv.invitation_id,'employee_id',inv.employee_id,'project_id',project,'position_id',inv.position_id,'terms_version',inv.terms_version,'terms_hash',inv.terms_hash,'terms_snapshot',inv.terms_snapshot));
  PERFORM private.event('invitation',inv.invitation_id,upper(action)||'_REQUESTED'); RETURN jsonb_build_object('ok',true,'invitation_id',inv.invitation_id,'pending',true);
 ELSIF action='invitation.respond' THEN
  PERFORM private.assert_role('EMPLOYEE');
  SELECT employee_id INTO employee FROM private.accounts WHERE account_id=actor AND owner_region=n AND active;
  SELECT * INTO resp FROM global.invitation_responses WHERE owner_region=n AND invitation_id=(p->>'invitation_id')::uuid;
  IF NOT FOUND OR employee IS NULL OR resp.employee_id<>employee THEN RAISE EXCEPTION 'Можно отвечать только за себя на домашнем узле' USING ERRCODE='42501'; END IF;
  PERFORM private.guard(employee);
  SELECT * INTO resp FROM global.invitation_responses WHERE owner_region=n AND invitation_id=resp.invitation_id FOR UPDATE;
  IF resp.decision<>'PENDING' THEN RAISE EXCEPTION 'Приглашение уже обработано' USING ERRCODE='23514'; END IF;
  IF resp.terms_version<>COALESCE((p->>'terms_version')::bigint,0) OR resp.terms_hash<>COALESCE(p->>'terms_hash','') THEN RAISE EXCEPTION 'Версия условий изменилась' USING ERRCODE='23514'; END IF;
  IF p->>'decision' NOT IN('ACCEPTED','DECLINED') THEN RAISE EXCEPTION 'Неизвестный ответ' USING ERRCODE='23514'; END IF;
  IF p->>'decision'='ACCEPTED' THEN
   replacement:=(resp.terms_snapshot->>'replaces_invitation_id')::uuid;
   IF replacement IS NOT NULL THEN SELECT * INTO old_a FROM global.assignments WHERE owner_region=n AND invitation_id=replacement AND employee_id=employee AND status IN('ACTIVE','CONFLICT'); IF NOT FOUND THEN RAISE EXCEPTION 'Назначение для замены отсутствует' USING ERRCODE='23514'; END IF; END IF;
   accepted:=private.requirements_fit(employee,resp.terms_snapshot) AND private.calendar_fits(employee,(resp.terms_snapshot->>'date_from')::date,(resp.terms_snapshot->>'date_to')::date,(resp.terms_snapshot->>'hours_per_week')::numeric,replacement);
   IF NOT accepted THEN
    PERFORM private.event('invitation',resp.invitation_id,'INVITATION.ACCEPT_REJECTED',jsonb_build_object('reason','CAPACITY_OR_REQUIREMENTS'));
    RETURN jsonb_build_object('ok',false,'error','CAPACITY_OR_REQUIREMENTS','message','Недостаточно доступных часов или не выполнены обязательные требования.');
   END IF;
   IF replacement IS NOT NULL THEN
    UPDATE global.assignments SET status='CANCELLED',updated_at=clock_timestamp() WHERE owner_region=n AND assignment_id=old_a.assignment_id;
    UPDATE global.invitation_responses SET decision='REPLACED',source_version=source_version+1,updated_at=clock_timestamp() WHERE owner_region=n AND invitation_id=replacement;
    PERFORM private.event('assignment',old_a.assignment_id,'ASSIGNMENT.REPLACED',jsonb_build_object('replacement_invitation_id',resp.invitation_id));
    PERFORM private.enqueue(old_a.project_region,'RESULT',private.response_result(replacement));
   END IF;
   id:=gen_random_uuid();
   INSERT INTO global.assignments(owner_region,assignment_id,employee_id,invitation_id,project_region,project_id,position_id,terms_version,date_from,date_to,hours_per_week) VALUES(n,id,employee,resp.invitation_id,resp.project_region,resp.project_id,resp.position_id,resp.terms_version,(resp.terms_snapshot->>'date_from')::date,(resp.terms_snapshot->>'date_to')::date,(resp.terms_snapshot->>'hours_per_week')::numeric);
   PERFORM private.event('assignment',id,'ASSIGNMENT.CREATED',jsonb_build_object('invitation_id',resp.invitation_id));
  END IF;
  UPDATE global.invitation_responses SET decision=p->>'decision',decision_at=clock_timestamp(),assignment_id=id,source_version=source_version+1,updated_at=clock_timestamp() WHERE owner_region=n AND invitation_id=resp.invitation_id;
  PERFORM private.event('invitation',resp.invitation_id,'INVITATION.'||(p->>'decision'),jsonb_build_object('terms_version',resp.terms_version,'terms_hash',resp.terms_hash));
  PERFORM private.enqueue(resp.project_region,'RESULT',private.response_result(resp.invitation_id));
  RETURN jsonb_build_object('ok',true,'invitation_id',resp.invitation_id,'assignment_id',id,'decision',p->>'decision');
 ELSIF action IN('review.create','review.update') THEN
  IF p->>'comment' ~* '[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}' OR p->>'comment' ~ '[+]?[0-9][0-9 ()-]{8,}[0-9]' THEN RAISE EXCEPTION 'Не включайте личные контакты в рабочий отзыв' USING ERRCODE='23514'; END IF;
  IF action='review.create' THEN
   SELECT * INTO inv FROM global.invitations WHERE owner_region=n AND invitation_id=(p->>'invitation_id')::uuid;
   IF NOT FOUND OR inv.assignment_status IS DISTINCT FROM 'COMPLETED' OR inv.status<>'COMPLETED' THEN RAISE EXCEPTION 'Отзыв доступен после подтвержденного завершения участия' USING ERRCODE='23514'; END IF;
   SELECT project_id INTO project FROM global.project_positions WHERE owner_region=n AND position_id=inv.position_id; proj:=private.manager_project(project);
   id:=COALESCE((p->>'review_id')::uuid,gen_random_uuid());
   INSERT INTO global.reviews(owner_region,review_id,project_id,employee_region,employee_id,assignment_id,author_id,rating,comment) VALUES(n,id,project,inv.employee_region,inv.employee_id,inv.assignment_id,actor,(p->>'rating')::integer,btrim(p->>'comment')) RETURNING to_jsonb(reviews) INTO row_json;
  ELSE
   SELECT * INTO rev FROM global.reviews WHERE owner_region=n AND review_id=(p->>'review_id')::uuid FOR UPDATE;
   IF NOT FOUND OR rev.author_id<>actor THEN RAISE EXCEPTION 'Редактировать отзыв может его автор' USING ERRCODE='42501'; END IF;
   proj:=private.manager_project(rev.project_id); id:=rev.review_id; old_json:=to_jsonb(rev);
   IF rev.version<>COALESCE((p->>'version')::bigint,-1) THEN RAISE EXCEPTION 'Отзыв изменен, обновите страницу' USING ERRCODE='40001'; END IF;
   UPDATE global.reviews SET rating=(p->>'rating')::integer,comment=btrim(p->>'comment'),version=version+1,updated_at=clock_timestamp() WHERE owner_region=n AND review_id=id RETURNING to_jsonb(reviews) INTO row_json;
  END IF;
  PERFORM private.event('review',id,upper(action),jsonb_build_object('before',old_json,'after',row_json),COALESCE((row_json->>'version')::bigint,1)); RETURN jsonb_build_object('ok',true,'review_id',id);
 ELSIF action IN('competency.create','competency.update') THEN
  PERFORM private.assert_role('CATALOG_ADMIN'); IF n<>0 THEN RAISE EXCEPTION 'Каталог изменяется на центральном узле' USING ERRCODE='42501'; END IF;
  id:=COALESCE((p->>'competency_id')::uuid,gen_random_uuid());
  IF action='competency.create' THEN INSERT INTO cat.competencies(competency_id,name,kind,description) VALUES(id,btrim(p->>'name'),p->>'kind',COALESCE(p->>'description',''));
  ELSE UPDATE cat.competencies SET name=COALESCE(btrim(p->>'name'),name),kind=COALESCE(p->>'kind',kind),description=COALESCE(p->>'description',description),active=COALESCE((p->>'active')::boolean,active),updated_at=clock_timestamp() WHERE competency_id=id; IF NOT FOUND THEN RAISE EXCEPTION 'Компетенция не найдена' USING ERRCODE='P0002'; END IF;
  END IF;
  PERFORM private.event('competency',id,upper(action)); RETURN jsonb_build_object('ok',true,'competency_id',id);
 END IF;
 RAISE EXCEPTION 'Неизвестная операция: %',action USING ERRCODE='22023';
END $$;
