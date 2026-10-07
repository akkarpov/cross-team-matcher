\set ON_ERROR_STOP on
-- A fresh database with no subscriptions is not a complete search catalog.
-- Upgrade the already-installed function without rewriting applied migrations.
DO $$ DECLARE definition text; needle text := 'SELECT NOT EXISTS(SELECT FROM pg_subscription_rel WHERE srsubstate<>''r'') INTO ready;'; BEGIN
 definition:=pg_get_functiondef('search.find_candidates(date,date,numeric,uuid[],integer,text,integer,text)'::regprocedure);
 IF position(needle IN definition)=0 THEN RAISE EXCEPTION 'Unexpected search function revision'; END IF;
 EXECUTE replace(definition,needle,'SELECT count(*)=2 AND NOT EXISTS(SELECT FROM pg_subscription_rel WHERE srsubstate<>''r'') INTO ready FROM pg_subscription;');
END $$;

CREATE FUNCTION private.freeze_workflow_identity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF TG_TABLE_NAME='invitations' THEN
  IF (NEW.owner_region,NEW.invitation_id,NEW.position_id,NEW.employee_region,NEW.employee_id,NEW.terms_version,NEW.terms_snapshot,NEW.terms_hash,NEW.replaces_invitation_id)
   IS DISTINCT FROM (OLD.owner_region,OLD.invitation_id,OLD.position_id,OLD.employee_region,OLD.employee_id,OLD.terms_version,OLD.terms_snapshot,OLD.terms_hash,OLD.replaces_invitation_id) THEN
   RAISE EXCEPTION 'Идентичность и отправленные условия оферты неизменяемы' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='invitation_responses' THEN
  IF (NEW.owner_region,NEW.invitation_id,NEW.employee_id,NEW.project_region,NEW.project_id,NEW.position_id,NEW.terms_version,NEW.terms_snapshot,NEW.terms_hash)
   IS DISTINCT FROM (OLD.owner_region,OLD.invitation_id,OLD.employee_id,OLD.project_region,OLD.project_id,OLD.position_id,OLD.terms_version,OLD.terms_snapshot,OLD.terms_hash) THEN
   RAISE EXCEPTION 'Полученный снимок условий неизменяем' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='reviews' THEN
  IF (NEW.owner_region,NEW.review_id,NEW.project_id,NEW.employee_region,NEW.employee_id,NEW.assignment_id,NEW.author_id,NEW.created_at)
   IS DISTINCT FROM (OLD.owner_region,OLD.review_id,OLD.project_id,OLD.employee_region,OLD.employee_id,OLD.assignment_id,OLD.author_id,OLD.created_at) THEN
   RAISE EXCEPTION 'Ссылки отзыва неизменяемы' USING ERRCODE='23514'; END IF;
 END IF; RETURN NEW;
END $$;
DO $$ DECLARE n smallint:=private.node_region(); t text; BEGIN IF n IN(1,2) THEN FOREACH t IN ARRAY ARRAY['invitations','invitation_responses','reviews'] LOOP
 EXECUTE format('CREATE TRIGGER freeze_workflow_identity BEFORE UPDATE ON r%s_data.%I FOR EACH ROW EXECUTE FUNCTION private.freeze_workflow_identity()',n,t);
END LOOP; END IF; END $$;
REVOKE ALL ON FUNCTION private.freeze_workflow_identity() FROM PUBLIC;
