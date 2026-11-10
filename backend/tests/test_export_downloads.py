from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest
import sys
import openpyxl
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from export_downloads import download_snapshot,download_directories,locate_fresh_export
from ziniao_exports import url_steps

class DownloadTests(unittest.TestCase):
    def write_xlsx(self,path):
        w=openpyxl.Workbook();w.active.append(['完整文件']);w.save(path)

    def test_only_new_complete_files_are_accepted_and_ambiguity_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'SELLER_INSIGHTS_VENDOR_ITEM_METRICS_old.xlsx';self.write_xlsx(old)
            baseline=download_snapshot('sales_daily',[root]);started=datetime.now(timezone.utc).isoformat()
            self.assertIsNone(locate_fresh_export('sales_daily',started,[root],baseline))
            new=root/'SELLER_INSIGHTS_VENDOR_ITEM_METRICS_new.xlsx';self.write_xlsx(new)
            self.assertEqual(locate_fresh_export('sales_daily',started,[root],baseline),new.resolve())
            newer=root/'SELLER_INSIGHTS_VENDOR_ITEM_METRICS_other.xlsx';self.write_xlsx(newer)
            with self.assertRaises(ValueError):locate_fresh_export('sales_daily',started,[root],baseline)

    def test_partial_files_and_other_store_directory_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'ziniao browser';a=base/'A';b=base/'B';a.mkdir(parents=True);b.mkdir()
            started=datetime.now(timezone.utc).isoformat()
            self.write_xlsx(b/'inventory_health_sku_info_b.xlsx')
            directories=download_directories({'store_name':'A','download_dirs':[str(base)]})
            self.assertEqual(directories,[a.resolve()])
            partial=a/'inventory_health_sku_info_partial.xlsx';partial.write_bytes(b'not complete zip')
            self.assertIsNone(locate_fresh_export('inventory',started,directories,{}))

    def test_only_supported_urls_and_matching_report_dates(self):
        step=url_steps('https://wing.coupang.com/tenants/rfm-inventory/management/list','2026-11-03')
        self.assertEqual(step[0]['kind'],'inventory')
        daily=url_steps('https://wing.coupang.com/tenants/business-insight/sales-analysis?start_date=2026-11-02&end_date=2026-11-02','2026-11-03')
        self.assertEqual(daily[0]['kind'],'sales_daily')
        for url in ['https://example.com/tenants/rfm-inventory/management/list','https://wing.coupang.com/other','https://wing.coupang.com/tenants/business-insight/sales-analysis?start_date=2026-11-02&start_date=2026-11-01&end_date=2026-11-02']:
            with self.assertRaises(ValueError):url_steps(url,'2026-11-03')

if __name__=='__main__':unittest.main()
