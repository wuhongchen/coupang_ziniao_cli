import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supplement_collection import Collector, check_data_access, insight_state
from ziniao_preflight import PagePermissionDenied


class InsightAccessTests(unittest.TestCase):
    def test_data_wins_over_subscription_promotion(self):
        for section, label in [('category', 'Top竞争商品'), ('keywords', '搜索曝光量')]:
            text = label + '\n请订阅商业洞察'
            check_data_access({'text': text})
            self.assertEqual(insight_state(section, text), 'ready')

    def test_loading_promotion_and_insufficient_data(self):
        self.assertIsNone(insight_state('category', '免费试用商业洞察'))
        self.assertIsNone(insight_state('keywords', '关键词 流入渠道'))
        self.assertEqual(insight_state('keywords', '搜索词分析数据不足\n订阅商业洞察，查看强化版数据'), 'insufficient_data')

    def test_delayed_subscription_prompt(self):
        collector = object.__new__(Collector)
        collector.ev = lambda _: {'text': '加载中'}
        screens = iter(['加载中', '订阅商业洞察，查看强化版数据。'])
        with patch('supplement_collection.time.sleep'), self.assertRaises(PagePermissionDenied):
            collector.wait(lambda: insight_state('keywords', next(screens)))

    def test_denied_vs_session_errors(self):
        for message in ['Forbidden', '权限不足', '您没有权限', '접근 권한이 없습니다']:
            with self.assertRaises(PagePermissionDenied):
                check_data_access({'text': message})
        for info in [{'url': 'https://xauth.coupang.com/login'}, {'captcha': True}, {'text': '503 Service Unavailable'}]:
            with self.assertRaises(RuntimeError) as error:
                check_data_access(info)
            self.assertNotIsInstance(error.exception, PagePermissionDenied)

    def test_regained_access_clears_old_skip(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'operations-cli'))
        from run import collect_supplement
        profile = dict(browser_id='1', store_id='ziniao:1', store_name='测试', expected_title='测试')
        with tempfile.TemporaryDirectory() as tmp, patch.object(Collector, 'pages', side_effect=[PagePermissionDenied('无权限'), None]) as pages:
            args = (Path(tmp),profile,'category','2026-11-11')
            self.assertEqual(collect_supplement(*args,category_id='1906')['status'],'skipped_permission')
            result = collect_supplement(*args,category_id='1906')
            self.assertEqual(result['status'],'complete')
            self.assertNotIn('error',result)
            self.assertEqual(pages.call_count,2)
