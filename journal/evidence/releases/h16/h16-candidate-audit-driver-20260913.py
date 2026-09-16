"""Read-only substantive H16 candidate checks; no remote client or state mutation."""
import argparse
import json
import resource
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from research.v4_candidate_acceptance import audit_structure
from research.v4_identity_checks import audit_identity
from swingset.build.closure import hydrate, validate
from swingset.build.generations import completed
from swingset.build.schema import SCHEMAS
from swingset.publish.safety import verify_candidate_files

V4 = '81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653'

def count(conn, sql, args=None):
    return int(conn.execute(sql, args or []).fetchone()[0])

def view(conn, prefix, table, path):
    glob = str(path/'data'/table/'*.parquet').replace("'", "''")
    conn.execute(f"CREATE VIEW {prefix}_{table} AS SELECT * FROM read_parquet('{glob}',hive_partitioning=false)")

def coverage_transitions(conn, run_id):
    """Account for each old year row using only this candidate's emitted delta."""
    old={("year",str(row[0]),row[1],row[2]) for row in conn.execute('SELECT year,source,via FROM old_coverage').fetchall()}
    current={tuple(row) for row in conn.execute("SELECT scope_kind,scope_id,source,via FROM new_coverage WHERE scope_kind='year'").fetchall()}
    changes={}
    for key,kind,field,before,after in conn.execute("SELECT record_key,change_type,field,old_value,new_value FROM new_changelog WHERE \"table\"='coverage' AND run_id=? AND (field IS NULL OR field IN ('scope_kind','scope_id'))",[run_id]).fetchall():
        changes.setdefault(tuple(json.loads(key)),[]).append((kind,field,json.loads(before),json.loads(after)))
    missing=[]
    for key in sorted(old):
        rows=changes.get(key,[])
        if key in current:
            valid=all(any(kind=='updated' and field==name and before is None and after==value for kind,field,before,after in rows) for name,value in [('scope_kind','year'),('scope_id',key[1])])
        else:
            valid=any(kind=='removed' and field is None and isinstance(before,dict) and str(before.get('year'))==key[1] and before.get('source')==key[2] and before.get('via')==key[3] and after is None for kind,field,before,after in rows)
        if not valid:
            missing.append(list(key))
    return {'baseline_year_rows':len(old),'retained_year_rows':len(old & current),'removed_year_rows':len(old-current),'unaccounted_count':len(missing),'unaccounted_examples':missing[:20]}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('state','candidate','baseline','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--temp-parent',type=Path,default=Path('/var/tmp'))
    args=parser.parse_args()
    state,candidate,baseline,output=(p.resolve() for p in (args.state,args.candidate,args.baseline,args.output))
    if output.exists() or args.output.is_symlink() or any(output.is_relative_to(p) for p in (state,candidate,baseline)) or output.is_relative_to('/var/lib/swingset'):
        raise ValueError('new output outside retained state and artifacts required')
    verify_candidate_files(candidate)
    verify_candidate_files(baseline)
    manifest=json.loads((candidate/'_meta/manifest.json').read_bytes())
    published=json.loads((baseline/'PUBLISHED').read_bytes())
    if published['commit']!=V4:
        raise ValueError('expected acknowledged V4 baseline')
    started=time.monotonic()
    report={'state':str(state),'candidate':str(candidate),'baseline_commit':V4,'network_requests':0,'checks':{},'details':{}}
    checks,details=report['checks'],report['details']
    with tempfile.TemporaryDirectory(prefix='h16-audit-',dir=args.temp_parent) as temporary, closing(duckdb.connect()) as db, closing(sqlite3.connect((state/'state.sqlite').as_uri()+'?mode=ro',uri=True)) as retained:
        retained.row_factory=sqlite3.Row
        retained.execute('BEGIN')
        db.execute("SET memory_limit='1GB'")
        db.execute("SET threads=2")
        db.execute("SET temp_directory='"+temporary.replace("'","''")+"'")
        for table,schema in SCHEMAS.items():
            files=sorted((candidate/'data'/table).glob('*.parquet'))
            if not files or any(not pq.read_schema(file).equals(schema) for file in files):
                raise ValueError('unexpected candidate schema: '+table)
            view(db,'new',table,candidate)
            # V4 lacks H16 public fields; old views intentionally retain old schema.
            view(db,'old',table,baseline)
            checks['row_count_'+table]=count(db,f'SELECT count(*) FROM new_{table}')==manifest['row_counts'][table]
        transitions=coverage_transitions(db,manifest['run_id'])
        details['coverage_schema_transition']=transitions
        checks['baseline_66_coverage_year_rows']=transitions['baseline_year_rows']==66
        checks['all_legacy_coverage_year_transitions_accounted']=transitions['unaccounted_count']==0
        structural=audit_structure(db)
        checks.update(structural['checks'])
        details['structure']=structural['details']
        policy=manifest['release_policy']
        checks['ordinary_closure']=policy.get('mode')=='closure'
        public=policy['closure']
        checks['compact_private_proof_only']=set(public)=={'format','private_digest','cutoff','selected_generations','support_token','inventory_digest','baseline','digest'} and public['format']=='release-closure-public-v1'
        validate(retained,public)
        private=hydrate(retained,public)
        checks['nonempty_structural_closure']=bool(private['selected'])
        details['selected_generation_count']=len(private['selected'])
        details['source_support_count']=len(private['source_support'])
        del private
        built=json.loads((candidate/'BUILT').read_bytes())
        report.update(candidate_id=candidate.name,manifest_hash=built['manifest_hash'])
        checks['durable_build_completion']=completed(retained,candidate.name,built['manifest_hash'])
        identity=audit_identity(db,release_policy=policy,baseline_commit=V4,state=retained)
        checks.update(identity['checks'])
        details['identity']=identity['details']
        checks['baseline_named_judge_count']=count(db,"SELECT count(*) FROM old_judges WHERE name_raw IS NOT NULL AND name_raw<>''")==4931
        checks['all_baseline_named_judges_preserved']=count(db,"SELECT count(*) FROM old_judges o LEFT JOIN new_judges n USING(judge_id) WHERE o.name_raw IS NOT NULL AND o.name_raw<>'' AND (n.judge_id IS NULL OR n.name_raw IS DISTINCT FROM o.name_raw OR (o.wsdc_id IS NULL AND n.wsdc_id IS NOT NULL))")==0
        details['default_entry_ids']={prefix:count(db,f'SELECT count(*) FROM {prefix}_entries WHERE wsdc_id IS NOT NULL') for prefix in ('old','new')}
        checks['baseline_entry_id_count']=details['default_entry_ids']['old']==34955
        occurrences=[tuple(row) for row in retained.execute("SELECT DISTINCT series_id,substr(event_month,1,7) FROM registry_placements WHERE event_month>='2010-01' ORDER BY 1,2")]
        db.execute('CREATE TEMP TABLE retained_occurrences(series_id VARCHAR,event_month VARCHAR)')
        if occurrences:
            db.executemany('INSERT INTO retained_occurrences VALUES (?,?)',occurrences)
        checks['one_event_per_retained_occurrence']=count(db,"SELECT count(*) FROM (SELECT r.series_id,r.event_month,count(e.event_id) n FROM retained_occurrences r LEFT JOIN new_events e ON e.series_id=r.series_id AND e.event_month=r.event_month GROUP BY 1,2 HAVING n<>1)")==0
        checks['registry_points_to_matching_occurrence']=count(db,"SELECT count(*) FROM new_registry_placements r LEFT JOIN new_events e USING(event_id) WHERE CAST(r.event_month AS VARCHAR)>='2010-01' AND (e.event_id IS NULL OR e.series_id IS DISTINCT FROM r.series_id OR e.event_month IS DISTINCT FROM substr(CAST(r.event_month AS VARCHAR),1,7))")==0
        details['retained_registry_occurrences']=len(occurrences)
        checks['all_17_years_disclosed']=count(db,"SELECT count(DISTINCT year) FROM new_coverage WHERE scope_kind='year' AND year BETWEEN 2010 AND 2026")==17
        checks['no_unreviewed_year_acceptance']=count(db,'SELECT count(*) FROM new_coverage WHERE events_accepted')==0
        checks['coverage_cutoff_matches']=count(db,'SELECT count(*) FROM new_coverage WHERE evidence_cutoff IS DISTINCT FROM CAST(? AS TIMESTAMPTZ)',[public['cutoff']])==0
        checks['honest_discovery_denominator']=count(db,"SELECT count(*) FROM new_coverage WHERE discovery_denominator IS NOT NULL OR discovery_universe IS DISTINCT FROM 'unknown'")==0
        checks['coverage_denominator_chain']=count(db,'SELECT count(*) FROM new_coverage WHERE acquisition_denominator IS DISTINCT FROM discovered_units OR interpretation_denominator IS DISTINCT FROM acquired_units OR mapping_denominator IS DISTINCT FROM interpreted_units')==0
        # Independent SQL recount from emitted rows, including all source/via event supports.
        branches=[]
        for table,key,event in [('events','event_id','t.event_id'),('contests','contest_id','t.event_id'),('rounds','round_id','c.event_id'),('entries','entry_id','t.event_id'),('registry_placements','event_id','t.event_id'),('judges','judge_id','t.event_id')]:
            metric='events' if table=='registry_placements' else 'identity_subjects' if table=='judges' else table
            extra=' LEFT JOIN new_contests c USING(contest_id)' if table=='rounds' else ''
            where=' WHERE t.event_id IS NOT NULL' if table=='registry_placements' else ''
            branches.append(f"SELECT '{metric}' metric,CAST(t.{key} AS VARCHAR) id,{event} event_id,COALESCE(t.source,'unknown') AS source,COALESCE(s.via,CASE WHEN t.snapshot_id='override' THEN 'manual' ELSE 'origin' END) via FROM new_{table} t{extra} LEFT JOIN new_snapshots s ON s.snapshot_id=t.snapshot_id{where}")
        db.execute('CREATE TEMP VIEW facts AS '+' UNION ALL '.join(branches))
        db.execute("CREATE TEMP VIEW scope_facts AS SELECT 'source' scope_kind,source scope_id,source,via,metric,id,event_id FROM facts UNION ALL SELECT 'event',f.event_id,f.source,f.via,f.metric,f.id,f.event_id FROM facts f JOIN new_events e USING(event_id) UNION ALL SELECT 'year',CAST(e.year AS VARCHAR),f.source,f.via,f.metric,f.id,f.event_id FROM facts f JOIN new_events e USING(event_id) WHERE e.year IS NOT NULL")
        db.execute("CREATE TEMP VIEW actual_coverage AS SELECT scope_kind,scope_id,source,via,count(DISTINCT event_id) events,count(DISTINCT id) FILTER(WHERE metric='contests') contests,count(DISTINCT id) FILTER(WHERE metric='rounds') rounds,count(DISTINCT id) FILTER(WHERE metric='entries') entries FROM scope_facts GROUP BY 1,2,3,4")
        differences=' OR '.join(f'COALESCE(c.{metric},0)<>COALESCE(a.{metric},0)' for metric in ('events','contests','rounds','entries'))
        checks['coverage_counts_match_emitted_facts']=count(db,'SELECT count(*) FROM new_coverage c FULL OUTER JOIN actual_coverage a USING(scope_kind,scope_id,source,via) WHERE '+differences)==0
    report.update(passed=all(checks.values()),elapsed_seconds=time.monotonic()-started,max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(report,stream,indent=2,sort_keys=True)
        stream.write('\n')
    if not report['passed']:
        raise SystemExit('Failed checks: '+', '.join(key for key,value in checks.items() if not value))

if __name__=='__main__':
    main()
