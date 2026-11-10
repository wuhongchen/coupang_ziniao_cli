import os
from unittest.mock import patch
import sys,json,hashlib,tempfile,unittest
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
class FeeCadenceTests(unittest.TestCase):
 def setUp(self):
  self.clock=patch.dict(os.environ,{'WING_TIMEZONE':'Etc/GMT-8'});self.clock.start()
 def tearDown(self):self.clock.stop()
 def fixture(self,root):
  (root/'state').mkdir();file=root/'state/fee.xlsx';file.write_bytes(b'fee');d={'store_id':'ziniao:1','steps':{'仓储费率报':{'status':'complete','file':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'captured_at':'2026-11-08T10:00:00+08:00'}}};(root/'state/supplement_run.json').write_text(json.dumps(d));return file
 def test_weekly_is_rolling_seven_days_not_calendar_week(self):
  from fee_cadence import reusable
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);self.fixture(root);profile={'store_id':'ziniao:1','store_name':'店'}
   r=reusable(root,profile,'仓储费率报',date(2026,11,9),7);self.assertTrue(r['cadence']['reused']);self.assertEqual(r['cadence']['next_due_date'],'2026-11-15')
   self.assertIsNone(reusable(root,profile,'仓储费率报',date(2026,11,15),7))
   self.assertIsNone(reusable(root,profile,'仓储费率报',date(2026,11,9),1))
 def test_corrupt_previous_file_is_not_silently_replaced(self):
  from fee_cadence import reusable
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);f=self.fixture(root);f.write_bytes(b'corrupt')
   with self.assertRaises(ValueError):reusable(root,{'store_id':'ziniao:1','store_name':'店'},'仓储费率报',date(2026,11,9),7)
 def test_other_store_or_fee_is_not_reused(self):
  from fee_cadence import reusable
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);self.fixture(root)
   self.assertIsNone(reusable(root,{'store_id':'ziniao:2','store_name':'店'},'仓储费率报',date(2026,11,9),7))
   self.assertIsNone(reusable(root,{'store_id':'ziniao:1','store_name':'店'},'销售手续费报告',date(2026,11,9),7))
 def test_legacy_manifest_is_reused_after_daily_checkpoint_rolls_over(self):
  from fee_cadence import reusable
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);self.fixture(root);d=json.loads((root/'state/supplement_run.json').read_text());e=d['steps']['仓储费率报'];old=Path(e['file']);new=root/'state/20261108_仓储费率报_hash.xlsx';old.rename(new);e['file']=str(new)
   path=root/'state/full_exports/2026-11-08_daily/manifest.json';path.parent.mkdir(parents=True);path.write_text(json.dumps({'store':'店','items':[e]}));(root/'state/supplement_run.json').unlink()
   r=reusable(root,{'store_id':'ziniao:1','store_name':'店'},'仓储费率报',date(2026,11,9),7);self.assertEqual(r['captured_at'],e['captured_at'])
