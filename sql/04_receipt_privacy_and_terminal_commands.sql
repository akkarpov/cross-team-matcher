\set ON_ERROR_STOP on
-- Original local command hashes remain useful for idempotency. Sensitive input
-- is consumed by the handler, then removed from the permanent transport record.
DO $$ DECLARE definition text; needle text; BEGIN
 definition:=pg_get_functiondef('private.process_command()'::regprocedure);
 needle:='UPDATE global.processed_commands SET result=result_json,processed_at=clock_timestamp()';
 IF position(needle IN definition)=0 THEN RAISE EXCEPTION 'Unexpected command handler revision'; END IF;
 EXECUTE replace(definition,needle,'UPDATE global.processed_commands SET result=result_json,payload=CASE WHEN NEW.kind IN(''LOCAL.employee.create'',''LOCAL.employee.update'') THEN NEW.payload-''full_name''-''contact_email''-''contact_phone'' ELSE NEW.payload END,processed_at=clock_timestamp()');
 definition:=replace(pg_get_functiondef('private.dispatch(text,jsonb)'::regprocedure),chr(13),'');
 definition:=replace(definition,'''error'',''CAPACITY_OR_REQUIREMENTS''','''error'',CASE WHEN private.requirements_fit(employee,resp.terms_snapshot) THEN ''capacity_exceeded'' ELSE ''payload_invalid'' END');
 definition:=replace(definition,'''Отзыв доступен после подтвержденного завершения участия''','''review_not_allowed: Отзыв доступен после подтвержденного завершения участия''');
 definition:=replace(definition,'''Версия условий изменилась''','''terms_mismatch: Версия условий изменилась''');
 needle:='ELSE
   UPDATE global.project_positions SET title=';
 needle:=replace(needle,chr(13),'');
 IF position(needle IN definition)=0 THEN RAISE EXCEPTION 'Unexpected position handler revision'; END IF;
 definition:=replace(definition,needle,'ELSE
   IF ((p?''hours_per_week'' AND (p->>''hours_per_week'')::numeric<>pos.hours_per_week)
       OR (p?''min_experience'' AND (p->>''min_experience'')::integer<>pos.min_experience)
       OR (p?''competency_ids'' AND (SELECT COALESCE(array_agg(value::uuid ORDER BY value::uuid),ARRAY[]::uuid[]) FROM jsonb_array_elements_text(p->''competency_ids'')) IS DISTINCT FROM (SELECT COALESCE(array_agg(competency_id ORDER BY competency_id),ARRAY[]::uuid[]) FROM global.position_competencies WHERE owner_region=n AND position_id=id)))
      AND EXISTS(SELECT FROM global.invitations WHERE owner_region=n AND position_id=id AND status IN(''PENDING'',''ACCEPTED'',''CANCELLING'')) THEN
    RAISE EXCEPTION ''terms_mismatch: Отправленные условия меняются через новую оферту и повторное согласие'' USING ERRCODE=''23514'';
   END IF;
   UPDATE global.project_positions SET title=');
 EXECUTE definition;
 definition:=pg_get_functiondef('global.mutate(text,jsonb)'::regprocedure);
 EXECUTE replace(definition,'''command_id уже использован для другого действия''','''command_conflict: command_id уже использован для другого действия''');
 definition:=pg_get_functiondef('private.receive(text,jsonb,smallint,uuid)'::regprocedure);
 needle:='IF r.decision NOT IN(''ACCEPTED'',''COMPLETED'') OR a.assignment_id IS NULL OR a.status NOT IN(''ACTIVE'',''CONFLICT'',''COMPLETED'') THEN RAISE EXCEPTION ''Нельзя завершить непринятое или отмененное участие'' USING ERRCODE=''23514''; END IF;';
 IF position(needle IN definition)=0 THEN RAISE EXCEPTION 'Unexpected terminal handler revision'; END IF;
 EXECUTE replace(definition,needle,'IF r.decision NOT IN(''ACCEPTED'',''COMPLETED'') OR a.assignment_id IS NULL OR a.status NOT IN(''ACTIVE'',''CONFLICT'',''COMPLETED'') THEN
 PERFORM private.enqueue(source,''RESULT'',private.response_result(inv_id),actor);
 PERFORM private.event(''invitation'',inv_id,''INVITATION.COMPLETE_REJECTED'',jsonb_build_object(''decision'',r.decision),r.source_version,actor);
 RETURN jsonb_build_object(''ok'',false,''error'',''payload_invalid'',''decision'',r.decision); END IF;');
END $$;

DO $$ DECLARE n smallint:=private.node_region(); target text; BEGIN
 target:=CASE WHEN n=0 THEN 'private.processed_commands' ELSE format('r%s_data.processed_commands',n) END;
 EXECUTE format('UPDATE %s SET payload=payload-''full_name''-''contact_email''-''contact_phone'' WHERE kind IN(''LOCAL.employee.create'',''LOCAL.employee.update'')',target);
 IF n IN(1,2) THEN
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY',target);
  EXECUTE format('CREATE POLICY transport_receipt_read ON %s FOR SELECT TO transport_c,transport_r1,transport_r2 USING(kind NOT LIKE ''LOCAL.%%'' AND source_region=CASE session_user WHEN ''transport_c'' THEN 0 WHEN ''transport_r1'' THEN 1 WHEN ''transport_r2'' THEN 2 ELSE -1 END)',target);
  EXECUTE format('CREATE POLICY transport_receipt_insert ON %s FOR INSERT TO transport_c,transport_r1,transport_r2 WITH CHECK(owner_region=%s AND kind IN(''OFFER'',''CANCEL'',''COMPLETE'',''RESULT'') AND source_region=CASE session_user WHEN ''transport_c'' THEN 0 WHEN ''transport_r1'' THEN 1 WHEN ''transport_r2'' THEN 2 ELSE -1 END)',target,n);
 END IF;
END $$;
