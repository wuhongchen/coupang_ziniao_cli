import json
import tempfile
import unittest
from pathlib import Path

from store_stats import ad_sources, add


class StatsTests(unittest.TestCase):
    def test_overlap_uses_newest_per_day_and_ignores_other_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, (store,start,end) in enumerate([('a','01','03'),('a','03','04'),('b','01','04')]):
                folder = root/'state/functions'/str(i)
                folder.mkdir(parents=True)
                run = {'store_id':store,'status':'complete','parameters':{'start':'2026-11-'+start,'end':'2026-11-'+end},
                       'steps':{'广告逐日关键词':{'status':'complete','file':str(i),'sha256':'hash',
                                           'captured_at':f'2026-11-0{i+5}T10:00:00+08:00'}}}
                (folder/'ads-keywords.json').write_text(json.dumps(run))
            days = ad_sources(root,'a','2026-11-02','2026-11-03')
            self.assertEqual({d:v[1] for d,v in days.items()},{'2026-11-02':'0','2026-11-03':'1'})

    def test_blank_and_zero_are_distinct(self):
        row = {}
        add(row,['广告点击','销售额'],[0,None])
        self.assertEqual(row,{'广告点击':0})
