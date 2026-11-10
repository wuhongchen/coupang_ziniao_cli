import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from export_profiles import configure_profile,load_profile

class ProfileTests(unittest.TestCase):
 def test_ads_identity_persists_without_download_dir(self):
  with tempfile.TemporaryDirectory() as t:
   configure_profile(t,'123','店','公司',ads_identity='A00000000')
   self.assertEqual(load_profile(t)['ads_identity'],'A00000000')
   with self.assertRaises(ValueError):configure_profile(t,'123','店','公司',ads_identity='OTHER')
   self.assertEqual(load_profile(t)['ads_identity'],'A00000000')
 def test_invalid_ads_identity_does_not_create_profile(self):
  with tempfile.TemporaryDirectory() as t:
   with self.assertRaises(ValueError):configure_profile(t,'123','店','公司',ads_identity='bad/value')
   self.assertFalse((Path(t)/'state/store_profile.json').exists())
