import copy
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from scripts.audit_minutes_features import audit, feature_buckets, load_helpers, readonly_query, read_records
from result_scopes import timestamp


def fixture(key='a', day='2026-11-01', player='Player One', stat='Points'):
    frozen = {'id':key,'event_id':'game-'+day,'created_at':day+'T19:00:00Z',
        'card':{'Platform':'prizepicks','Player':player,'Period':'result','Stat':stat,
                'Line':20.5,'Line Type':'Standard','Start (UTC)':day+'T23:00:00Z'},
        'result':{'direction':'MORE','minutes':30,'predictive_context':{
            'days_since_last_appearance':1,'minutes_shift':3},
            'teammate_context':{'available':True,'out_teammates':[{'name':'Other'}]}}}
    return frozen, {'status':'FINAL','outcome':'LOSS','minutes':35}


class MinutesFeatureAuditTests(unittest.TestCase):
    def test_buckets_boundaries_missing_and_future_context(self):
        created=timestamp('2026-11-01T19:00Z')
        for shift, label in ((-3,'Falling (≤ -3 min)'),(-2.99,'Stable (-3 to +3)'),
                             (2.99,'Stable (-3 to +3)'),(3,'Rising (≥ +3 min)'),(None,'Unavailable')):
            r={'predictive_context':{'minutes_shift':shift}}
            self.assertEqual(feature_buckets(r,created)['minutes_trend'],label)
        self.assertEqual(set(feature_buckets({},created).values()),{'Unavailable'})
        for days,label in ((1,'Back-to-back'),(2,'1+ days rest'),(0,'Unavailable')):
            self.assertEqual(feature_buckets({'predictive_context':{'days_since_last_appearance':days}},created)['rest'],label)
        self.assertEqual(feature_buckets({'predictive_context':{'last5_minutes':30,'previous5_minutes':34}},created)['minutes_trend'],'Falling (≤ -3 min)')
        self.assertEqual(feature_buckets({'teammate_context':{'available':True,'out_teammates':[]}},created)['teammate_out'],'0 out')
        frozen,_=fixture()
        self.assertEqual(feature_buckets(frozen['result'],created)['teammate_out'],'1+ out')
        frozen['result']['teammate_context']['checked_at']='2026-11-01T20:00Z'
        frozen['result']['predictive_context']['as_of']='2026-11-01T20:00Z'
        self.assertEqual(set(feature_buckets(frozen['result'],created).values()),{'Unavailable'})

    def test_bias_sign_unique_player_games_and_pra_fantasy_subset(self):
        records=[];grades={}
        for key,stat,minutes in (('a','Points',35),('b','Points + Rebounds + Assists',25),('c','Fantasy Score',33)):
            f,g=fixture(key,stat=stat);g['minutes']=minutes;g['outcome']='WIN' if key=='c' else 'LOSS'
            records.append((key,f));grades[key]=g
        before=copy.deepcopy((records,grades))
        report=audit(records,grades)
        self.assertEqual(report['readiness'],{'settled_picks':3,'distinct_player_games':1,'graded_dates':1,'ready_for_v3_fit':False})
        bucket=report['by_feature']['rest']['Back-to-back']
        self.assertEqual(bucket['player_games'],1)
        self.assertEqual(bucket['avg_minutes_bias'],1)
        self.assertAlmostEqual(bucket['minutes_mae'],13/3)
        self.assertAlmostEqual(bucket['v1_hit_rate'],1/3)
        subset=report['pra_and_fantasy_only']['rest']['Back-to-back']
        self.assertEqual(subset['picks'],2);self.assertEqual(subset['avg_minutes_bias'],-1)
        self.assertEqual((records,grades),before)

    def test_readiness_requires_both_ten_dates_and_300_player_games(self):
        records=[];grades={}
        for day in range(10):
            for player in range(30):
                key=f'{day}-{player}';f,g=fixture(key,(date(2026,11,1)+timedelta(days=day)).isoformat(),f'Player {player}')
                records.append((key,f));grades[key]=g
        self.assertTrue(audit(records,grades)['readiness']['ready_for_v3_fit'])
        self.assertFalse(audit(records[:-1],grades)['readiness']['ready_for_v3_fit'])
        for _,f in records:
            if f['card']['Start (UTC)'].startswith('2026-11-10'):
                f['card']['Start (UTC)']='2026-11-09T23:00Z';f['created_at']='2026-11-09T19:00Z'
                f['event_id']='different-game'
        report=audit(records,grades)['readiness']
        self.assertEqual(report['distinct_player_games'],300);self.assertEqual(report['graded_dates'],9)
        self.assertFalse(report['ready_for_v3_fit'])

    def test_scope_filters_and_dedup_before_minutes_filter(self):
        for change in ('Goblin','Demon','blocked','pending','tie','post','missing_minutes','nan_minutes'):
            f,g=fixture()
            if change in ('Goblin','Demon'):f['card']['Line Type']=change
            if change=='blocked':f['result']['selection_allowed']=False
            if change=='pending':g['status']='PENDING'
            if change=='tie':g['outcome']='TIE'
            if change=='post':f['created_at']=f['card']['Start (UTC)']
            if change=='missing_minutes':g.pop('minutes')
            if change=='nan_minutes':f['result']['minutes']=float('nan')
            self.assertEqual(audit([('a',f)],{'a':g})['readiness']['settled_picks'],0,change)
        first,g=fixture();first['result'].pop('minutes')
        later=copy.deepcopy(first);later['id']='b';later['created_at']='2026-11-01T20:00Z';later['result']['minutes']=30
        self.assertEqual(audit([('a',first),('b',later)],{'a':g,'b':g})['readiness']['settled_picks'],0)

    def test_cli_reads_existing_database_without_writes_or_app_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'audit.sqlite3'
            f,g=fixture();f['snapshot']={'bulky':'x'*1000};f['result']['games']='no history needed';f['result']['roster']={}
            with sqlite3.connect(path) as con:
                con.execute('CREATE TABLE sports_records(namespace TEXT,key TEXT,payload TEXT)')
                con.executemany('INSERT INTO sports_records VALUES(?,?,?)',
                                [('forecast','a',json.dumps(f)),('grade','a',json.dumps(g))])
            before=path.read_bytes()
            with readonly_query(str(path)) as (query,postgres):
                records,grades=read_records(query,postgres)
                self.assertNotIn('snapshot',records[0][1]);self.assertNotIn('games',records[0][1]['result'])
                with self.assertRaises(ValueError):query('DELETE FROM sports_records')
            script=Path(__file__).resolve().parents[1]/'scripts/audit_minutes_features.py'
            run=subprocess.run([sys.executable,str(script),'--database',str(path)],cwd=folder,capture_output=True,text=True,check=True)
            lines=run.stdout.splitlines();self.assertEqual(len(lines),1)
            self.assertTrue(lines[0].startswith('MINUTES_FEATURE_AUDIT_JSON '))
            self.assertEqual(json.loads(lines[0].split(' ',1)[1])['readiness']['settled_picks'],1)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(set(Path(folder).iterdir()),{path})
            with self.assertRaises(sqlite3.OperationalError):
                with readonly_query(str(Path(folder)/'missing.sqlite3')):pass
