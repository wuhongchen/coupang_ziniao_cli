import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook
from business_analysis import ads_section, render_report, verified


class BusinessReportTests(unittest.TestCase):
    def test_empty_store_is_not_zero_revenue(self):
        with tempfile.TemporaryDirectory() as folder:
            report = render_report(Path(folder), {'store_id':'local-test','store_name':'样例'})
        self.assertIn('尚无已核验数据', report)
        self.assertNotIn('销售额 ₩0', report)

    def test_weighted_ad_return(self):
        sheet = Workbook().active
        sheet.append(['广告活动ID','广告活动名','点击次数','广告费','总转化销售额（14天）'])
        sheet.append([1,'甲',4,90,0])
        sheet.append([2,'乙',2,10,50])
        self.assertIn('ROAS 0.50 倍', ads_section(sheet)[0])

    def test_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'state').mkdir()
            (root/'state/data').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                verified(root,'state/data','invalid')
