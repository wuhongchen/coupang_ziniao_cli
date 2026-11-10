import sys,tempfile,unittest
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'operations-cli'))

class PolicyTests(unittest.TestCase):
 def test_custom_sales_window(self):
  from sales_trend import dates,analyze
  days=dates('2020-01-04',3)
  self.assertEqual(days,['2020-01-04','2020-01-03','2020-01-02'])
  with self.assertRaises(ValueError):dates('2020-01-04',0)
  row={'id':'1','pid':'2','name':'商品','units':1,'revenue':2}
  result=analyze([{'start':d,'payload':[row],'provisional':False} for d in days],days)
  self.assertEqual(result['options'][0]['units_total'],3)
 def test_custom_fee_interval(self):
  from fee_cadence import metadata
  e={'captured_at':'2020-01-01T12:00:00+00:00'}
  self.assertEqual(metadata(e,date(2020,1,2),5,True)['next_due_date'],'2020-01-06')
  with self.assertRaises(ValueError):metadata(e,date(2020,1,2),0,True)
 def test_category_and_date_inputs(self):
  from supplement_collection import Collector
  with tempfile.TemporaryDirectory() as t:
   profile={'browser_id':'123','store_id':'ziniao:123','store_name':'示例','expected_title':'公司'}
   c=Collector(Path(t),profile,'2020-01-04',start='2020-01-01',end='2020-01-03',category_id='456')
   self.assertEqual(str(c.start),'2020-01-01');self.assertEqual(c.category_id,'456')
   with self.assertRaises(ValueError):Collector(Path(t),profile,'2020-01-04',start='2020-01-03',end='2020-01-01')
   with self.assertRaises(ValueError):Collector(Path(t),profile,'2020-01-04',category_id='bad')
 def test_missing_policy_never_touches_browser(self):
  import run as cli
  from unittest.mock import patch
  for command in (['advertising'],['analytics'],['bills'],['collect','category'],['collect','ads-keywords'],['collect','fee-storage']):
   with tempfile.TemporaryDirectory() as t,patch('ziniao_preflight.check') as check,patch('ziniao_preflight.invoke') as invoke:
    self.assertEqual(cli.main(['--output-root',t,*command]),1)
    check.assert_not_called();invoke.assert_not_called()
 def test_timezone_option_restores_environment(self):
  import os
  from types import SimpleNamespace
  from unittest.mock import patch
  from ziniao_environment import environment
  import runtime_clock as clock
  with patch.dict(os.environ,{'WING_TIMEZONE':'UTC'}):
   with environment(SimpleNamespace(timezone='Etc/GMT-3')):self.assertEqual(str(clock.timezone()),'Etc/GMT-3')
   self.assertEqual(os.environ['WING_TIMEZONE'],'UTC')
 def test_group_forwards_explicit_ranges(self):
  from groups import run_group
  from unittest.mock import patch
  profile={'browser_id':'123','store_id':'ziniao:123'}
  with tempfile.TemporaryDirectory() as t,patch('groups.execute',return_value=[{'status':'complete'}]) as execute:
   r=run_group(Path(t),profile,'analytics','2020-01-04',None,'2020-01-01','2020-01-03','456')
   self.assertEqual(r['status'],'complete')
   commands=[c.args[2] for c in execute.call_args_list]
   self.assertIn('--category-id',commands[1]);self.assertIn('456',commands[1])
   self.assertIn('--start',commands[2]);self.assertIn('2020-01-03',commands[2])
