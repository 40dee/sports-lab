import copy
import unittest
from grading_identity import lookup_actual

class GradingIdentityTests(unittest.TestCase):
 def setup_case(self):
  summary={'header':{'id':'game','competitions':[{'status':{'type':{'completed':True}},'competitors':[{'team':{'id':'team','displayName':'Boston Celtics'}}]}]},
   'boxscore':{'players':[{'team':{'id':'team'},'statistics':[{'athletes':[{'athlete':{'id':'new','displayName':'Austin Reaves'}}]}]}]}}
  return summary,{'Player':'Reaves, Austin'},{'player_id':'old','team':'Boston Celtics'}
 def run_case(self,s,c,r):
  return lookup_actual(s,c,r,'game',['PTS'],lambda summary,pid,cols:{'status':'FINAL','actual':12} if pid=='new' else None)
 def test_unique_full_name_team_and_event_recovery_without_mutation(self):
  s,c,r=self.setup_case();before=copy.deepcopy((s,c,r));out=self.run_case(s,c,r)
  self.assertEqual(out['actual'],12);self.assertEqual(out['identity_verification']['resolved_player_id'],'new')
  self.assertEqual((s,c,r),before)
 def test_surname_only_missing_wrong_team_event_and_ambiguous_are_unresolved(self):
  for change in ('surname','missing','team','event','duplicate','not_final'):
   s,c,r=self.setup_case()
   if change=='surname':c['Player']='Someone Reaves'
   if change=='missing':s['boxscore']['players']=[]
   if change=='team':r['team']='Other team'
   if change=='event':s['header']['id']='wrong'
   if change=='not_final':s['header']['competitions'][0]['status']['type']['completed']=False
   if change=='duplicate':s['boxscore']['players'][0]['statistics'][0]['athletes'].append({'athlete':{'id':'other','displayName':'Austin Reaves'}})
   self.assertIsNone(self.run_case(s,c,r),change)
 def test_value_error_falls_back_only_when_original_id_absent(self):
  s,c,r=self.setup_case()
  def extract(s,p,c):
   if p=='old':raise ValueError('Missing identity')
   return {'status':'FINAL','actual':0}
  self.assertEqual(lookup_actual(s,c,r,'game',['PTS'],extract)['actual'],0)
  s['boxscore']['players'][0]['statistics'][0]['athletes'].append({'athlete':{'id':'old','displayName':'Other'}})
  with self.assertRaises(ValueError):lookup_actual(s,c,r,'game',['PTS'],extract)
