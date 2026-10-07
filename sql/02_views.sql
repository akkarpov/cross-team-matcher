\set ON_ERROR_STOP on
-- These UNION ALL views intentionally name ordinary local tables, not global.
-- A disconnected owner therefore never blocks preliminary search or analytics.
CREATE VIEW search.employee_profiles AS SELECT e.*,e.owner_region AS source_region,e.updated_at AS source_updated_at,e.owner_region=private.node_region() AS is_local FROM r1_data.employees e UNION ALL SELECT e.*,e.owner_region,e.updated_at,e.owner_region=private.node_region() FROM r2_data.employees e;
DO $$ DECLARE t text; BEGIN FOREACH t IN ARRAY ARRAY['employee_competencies','capacity_periods','unavailability_periods','projects','project_positions','position_competencies','invitations','invitation_responses','assignments','workflow_events','reviews'] LOOP EXECUTE format('CREATE VIEW search.%I AS SELECT * FROM r1_data.%I UNION ALL SELECT * FROM r2_data.%I',t,t,t); END LOOP; END $$;

CREATE FUNCTION search.remaining_hours(employee_region smallint,employee uuid,first_day date,last_day date) RETURNS numeric LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,private AS $$
 SELECT CASE WHEN NOT private.valid_dates(first_day,last_day) THEN 0 ELSE COALESCE(min(
 CASE WHEN EXISTS(SELECT FROM search.unavailability_periods u WHERE u.owner_region=employee_region AND u.employee_id=employee AND u.active AND day::date BETWEEN u.date_from AND u.date_to) THEN 0 ELSE
 COALESCE((SELECT c.hours_per_week FROM search.capacity_periods c WHERE c.owner_region=employee_region AND c.employee_id=employee AND day::date BETWEEN c.date_from AND c.date_to),0) END
 - COALESCE((SELECT sum(a.hours_per_week) FROM search.assignments a WHERE a.owner_region=employee_region AND a.employee_id=employee AND a.status IN('ACTIVE','CONFLICT') AND day::date BETWEEN a.date_from AND a.date_to),0)),0) END
 FROM generate_series(first_day::timestamp,last_day::timestamp,interval '1 day') day WHERE extract(isodow FROM day)<6
$$;

CREATE FUNCTION search.find_candidates(first_day date,last_day date,requested_hours numeric,required_competencies uuid[],minimum_experience integer,region_mode text,page_number integer DEFAULT 1,query_text text DEFAULT '') RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,private AS $$
DECLARE own smallint; result_json jsonb; ready boolean;
BEGIN
 PERFORM private.assert_role('MANAGER');
 SELECT owner_region INTO own FROM private.accounts WHERE account_id=private.actor_id() AND active;
 IF region_mode NOT IN('OWN_FIRST','OWN_ONLY','ALL') OR NOT private.valid_dates(first_day,last_day) OR requested_hours NOT BETWEEN 0.5 AND 40 OR mod(requested_hours,0.5)<>0 OR minimum_experience<0 OR page_number<1 THEN RAISE EXCEPTION 'Некорректные параметры поиска' USING ERRCODE='22023'; END IF;
 SELECT NOT EXISTS(SELECT FROM pg_subscription_rel WHERE srsubstate<>'r') INTO ready;
 IF NOT ready THEN RETURN jsonb_build_object('items','[]'::jsonb,'total',0,'page',page_number,'page_size',25,'mode',region_mode,'own_region',own,'initial_sync_pending',true); END IF;
 WITH eligible AS MATERIALIZED(
  SELECT e.*,search.remaining_hours(e.owner_region,e.employee_id,first_day,last_day) AS available_hours_per_week,
   COALESCE((SELECT jsonb_agg(jsonb_build_object('competency_id',c.competency_id,'name',c.name,'kind',c.kind) ORDER BY c.name) FROM search.employee_competencies ec JOIN cat.competencies c USING(competency_id) WHERE ec.owner_region=e.owner_region AND ec.employee_id=e.employee_id AND c.active),'[]') AS competencies
  FROM search.employee_profiles e
  WHERE e.active AND e.experience_months>=minimum_experience AND (region_mode<>'OWN_ONLY' OR e.owner_region=own)
   AND (COALESCE(query_text,'')='' OR e.nickname ILIKE '%'||query_text||'%')
   AND NOT EXISTS(SELECT FROM unnest(COALESCE(required_competencies,ARRAY[]::uuid[])) requirement WHERE NOT EXISTS(SELECT FROM search.employee_competencies ec JOIN cat.competencies c USING(competency_id) WHERE ec.owner_region=e.owner_region AND ec.employee_id=e.employee_id AND ec.competency_id=requirement AND c.active))
 ), qualified AS MATERIALIZED(SELECT * FROM eligible WHERE available_hours_per_week>=requested_hours), paged AS(
  SELECT *,CASE WHEN region_mode='OWN_FIRST' AND owner_region=own THEN 0 ELSE 1 END AS region_rank FROM qualified
  ORDER BY CASE WHEN region_mode='OWN_FIRST' AND owner_region=own THEN 0 ELSE 1 END,available_hours_per_week DESC,experience_months DESC,owner_region,employee_id LIMIT 25 OFFSET (page_number-1)*25
 ) SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY region_rank,available_hours_per_week DESC,experience_months DESC,owner_region,employee_id) FROM paged p),'[]'),'total',(SELECT count(*) FROM qualified),'page',page_number,'page_size',25,'mode',region_mode,'own_region',own,'initial_sync_pending',false) INTO result_json;
 RETURN result_json;
END $$;

CREATE FUNCTION search.candidates(position_owner smallint,position_uuid uuid,mode text DEFAULT NULL,page integer DEFAULT 1) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,private AS $$
DECLARE pos record; requirements uuid[];
BEGIN
 SELECT p.*,pr.date_from,pr.date_to,pr.manager_id INTO pos FROM search.project_positions p JOIN search.projects pr ON pr.owner_region=p.owner_region AND pr.project_id=p.project_id WHERE p.owner_region=position_owner AND p.position_id=position_uuid;
 IF NOT FOUND OR pos.manager_id<>private.actor_id() THEN RAISE EXCEPTION 'Позиция недоступна менеджеру' USING ERRCODE='42501'; END IF;
 SELECT array_agg(competency_id) INTO requirements FROM search.position_competencies WHERE owner_region=position_owner AND position_id=position_uuid;
 RETURN search.find_candidates(pos.date_from,pos.date_to,pos.hours_per_week,requirements,pos.min_experience,COALESCE(mode,pos.search_mode),page,'');
END $$;

CREATE FUNCTION search.freshness() RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,private AS $$
 SELECT jsonb_build_object('node_region',private.node_region(),'checked_at',clock_timestamp(),'sources',COALESCE((SELECT jsonb_agg(jsonb_build_object('source_region',CASE WHEN s.subname LIKE '%r1%' THEN 1 WHEN s.subname LIKE '%r2%' THEN 2 ELSE 0 END,'subscription_name',s.subname,'enabled',s.subenabled,'ready',NOT EXISTS(SELECT FROM pg_subscription_rel r WHERE r.srsubid=s.oid AND r.srsubstate<>'r'),'last_received_at',st.last_msg_receipt_time,'last_message_at',st.last_msg_send_time,'latest_end_time',st.latest_end_time)) FROM pg_subscription s LEFT JOIN pg_stat_subscription st ON st.subid=s.oid AND st.relid IS NULL),'[]'::jsonb))
$$;

CREATE VIEW analytics.regional_summary AS
SELECT r.region_id,r.name,
 (SELECT count(*) FROM search.employee_profiles e WHERE e.owner_region=r.region_id AND e.active) AS employees,
 (SELECT count(*) FROM search.assignments a WHERE a.owner_region=r.region_id AND a.status='ACTIVE') AS active_assignments,
 (SELECT count(*) FROM search.assignments a WHERE a.owner_region=r.region_id AND a.status='CONFLICT') AS conflicts,
 (SELECT count(*) FROM search.assignments a WHERE a.owner_region=r.region_id AND a.status='COMPLETED') AS completed_assignments,
 (SELECT COALESCE(sum(a.hours_per_week),0) FROM search.assignments a WHERE a.owner_region=r.region_id AND a.status IN('ACTIVE','CONFLICT') AND current_date BETWEEN a.date_from AND a.date_to) AS assigned_hours_per_week,
 (SELECT round(avg(rw.rating),2) FROM search.reviews rw WHERE rw.employee_region=r.region_id) AS average_rating,
 (SELECT count(*) FROM search.reviews rw WHERE rw.employee_region=r.region_id) AS review_count
FROM cat.regions r WHERE r.region_id IN(1,2) AND private.node_region()=0 AND private.has_role('ANALYST');
CREATE VIEW analytics.project_summary AS
SELECT p.*,(SELECT count(*) FROM search.project_positions pos WHERE pos.owner_region=p.owner_region AND pos.project_id=p.project_id) AS positions,
 (SELECT count(*) FROM search.assignments a WHERE a.project_region=p.owner_region AND a.project_id=p.project_id AND a.status IN('ACTIVE','CONFLICT')) AS active_assignments,
 (SELECT count(*) FROM search.assignments a WHERE a.project_region=p.owner_region AND a.project_id=p.project_id AND a.status='COMPLETED') AS completed_assignments,
 (SELECT round(avg(r.rating),2) FROM search.reviews r WHERE r.owner_region=p.owner_region AND r.project_id=p.project_id) AS average_rating
FROM search.projects p WHERE private.node_region()=0 AND private.has_role('ANALYST');
CREATE VIEW analytics.invitation_funnel AS SELECT owner_region,status,count(*) AS count FROM search.invitations WHERE private.node_region()=0 AND private.has_role('ANALYST') GROUP BY owner_region,status;
CREATE VIEW analytics.competency_demand AS SELECT c.competency_id,c.name,c.kind,
 (SELECT count(*) FROM search.employee_competencies ec WHERE ec.competency_id=c.competency_id) AS employees,
 (SELECT count(*) FROM search.position_competencies pc WHERE pc.competency_id=c.competency_id) AS positions
FROM cat.competencies c WHERE private.node_region()=0 AND private.has_role('ANALYST');
GRANT SELECT ON ALL TABLES IN SCHEMA search,analytics TO matcher_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA private FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA search FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA search TO matcher_app;
GRANT EXECUTE ON FUNCTION private.node_region(),private.actor_id(),private.has_role(text,text),private.command_hash(jsonb) TO matcher_app,matcher_worker;
