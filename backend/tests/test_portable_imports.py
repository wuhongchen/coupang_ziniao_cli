import unittest,sys,importlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'operations-cli'))

class PortableImportTests(unittest.TestCase):
 def test_all_published_capability_modules_import(self):
  for name in ('run','groups','sales_trend','storage','publication','product_images','supplement_collection','review_collection'):
   with self.subTest(module=name):importlib.import_module(name)
