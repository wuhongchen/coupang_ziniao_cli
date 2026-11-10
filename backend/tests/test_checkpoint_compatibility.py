from pathlib import Path
import tempfile,json,sys,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'operations-cli'))
from run import collect_core

class CheckpointCompatibilityTests(unittest.TestCase):
 def test_derived_provisional_field_does_not_change_requested_period(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder=root/'state/functions/2020-01-03';folder.mkdir(parents=True)
   run={'store_id':'ziniao:1','status':'complete','steps':[{'kind':'sales_window','start':'2020-01-01','end':'2020-01-03','provisional':False}],'results':[]}
   (folder/'sales-window_2020-01-01_2020-01-03.json').write_text(json.dumps(run))
   with patch('ziniao_exports.configure_runtime'),patch('ziniao_exports.write'),patch('ziniao_exports.final_gate') as gate,patch('ziniao_exports.next_export') as download:
    result=collect_core(root,{'store_id':'ziniao:1'},'sales-window','2020-01-03','2020-01-01','2020-01-03')
    self.assertEqual(result['status'],'complete');gate.assert_called_once();download.assert_not_called()

 def test_different_period_is_still_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);folder=root/'state/functions/2020-01-03';folder.mkdir(parents=True)
   run={'store_id':'ziniao:1','status':'complete','steps':[{'kind':'sales_window','start':'2019-12-31','end':'2020-01-03','provisional':False}],'results':[]}
   (folder/'sales-window_2020-01-01_2020-01-03.json').write_text(json.dumps(run))
   with patch('ziniao_exports.configure_runtime'),patch('ziniao_exports.write') as write:
    with self.assertRaisesRegex(ValueError,'冲突'):collect_core(root,{'store_id':'ziniao:1'},'sales-window','2020-01-03','2020-01-01','2020-01-03')
    write.assert_not_called()
