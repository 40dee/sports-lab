import ast,json,sqlite3,unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

TREE=ast.parse(Path(__file__).resolve().parents[1].joinpath('app.py').read_text())
NAMES={'Store','TursoStore','OfflineSessionStore'}
env={'json':json,'encode':json.dumps,'now':lambda:'2026-11-18T10:00:00Z','contextmanager':contextmanager}
exec(compile(ast.Module(body=[n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name in NAMES],type_ignores=[]),'app.py','exec'),env)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE sports_records(namespace,key,payload,updated,PRIMARY KEY(namespace,key))')
        self.calls=[]
        def post(*args,**kwargs):
            self.calls.append(kwargs['json']);results=[]
            for request in kwargs['json']['requests']:
                if request['type']=='close':results.append({'type':'ok','response':{'type':'close'}});continue
                statement=request['stmt']
                self.db.execute(statement['sql'],[a['value'] for a in statement['args']])
                results.append({'type':'ok','response':{'type':'execute','result':{'rows':[]}}})
            self.db.commit()
            return SimpleNamespace(status_code=200,json=lambda:{'results':results})
        self.store=env['TursoStore'].__new__(env['TursoStore'])
        self.store.http=SimpleNamespace(post=post);self.store.endpoint='mock://pipeline';self.store.token='test'
        self.store._read_cache={('items','forecast',None):'stale'}
    def backup(self,count=123):
        return {'format':'sports-lab-workspace-v2','records':[
            ['forecast',str(i),json.dumps({'value':i}),'2026-11-18T09:00:00Z'] for i in range(count)]}
    def test_batched_restore_is_idempotent_and_preserves_original_records(self):
        self.db.execute('INSERT INTO sports_records VALUES(?,?,?,?)',('forecast','0','{"original":true}','old'))
        value=self.backup()
        self.assertEqual(self.store.restore(value),123)
        self.assertEqual(len(self.calls),3)
        self.assertTrue(all(len(call['requests'])<=51 for call in self.calls))
        self.assertEqual(json.loads(self.db.execute('SELECT payload FROM sports_records WHERE key="0"').fetchone()[0]),{'original':True})
        self.assertEqual(self.db.execute('SELECT count(*) FROM sports_records').fetchone()[0],123)
        self.store.restore(value)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sports_records').fetchone()[0],123)
        self.assertEqual(self.store._read_cache,{})
    def test_invalid_final_record_causes_zero_writes(self):
        value=self.backup();value['records'][-1][2]='bad json'
        with self.assertRaises(ValueError):self.store.restore(value)
        self.assertEqual(self.calls,[])
    def test_statement_error_is_not_reported_as_complete(self):
        self.store.http.post=Mock(return_value=SimpleNamespace(status_code=200,json=lambda:{'results':[{'type':'error'}]}))
        with self.assertRaisesRegex(RuntimeError,'Some rows may already be restored'):
            self.store.restore(self.backup(1))
        self.assertEqual(self.store._read_cache,{})
