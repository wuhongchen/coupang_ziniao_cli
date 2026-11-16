import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'operations-cli'))
from rocket_price import auto_result


class RocketAutoTest(unittest.TestCase):
    def setUp(self):
        self.before = {'auto_enabled': True, 'sale': 21000}
        self.after = {'auto_enabled': False, 'sale': 21000}
        self.query = Mock(return_value=self.after)
        self.disable = Mock()

    def run_action(self, action='auto-off', apply=True):
        return auto_result(action, self.before, self.query, self.disable, apply, 21000)

    def test_query_and_preview_do_not_write(self):
        self.assertEqual(self.run_action('auto-status')['status'], 'observed')
        self.assertEqual(self.run_action(apply=False)['status'], 'preview')
        self.disable.assert_not_called()

    def test_disabled_retry_does_not_toggle(self):
        self.before = self.after
        self.assertEqual(self.run_action()['status'], 'already_disabled')
        self.disable.assert_not_called()

    def test_stale_sale_prevents_toggle(self):
        self.before['sale'] += 100
        with self.assertRaises(ValueError):
            self.run_action()
        self.disable.assert_not_called()

    def test_submit_once_and_read_again(self):
        self.disable.side_effect = lambda: self.query.assert_not_called()
        self.assertEqual(self.run_action()['after'], self.after)
        self.disable.assert_called_once_with()
        self.query.assert_called_once_with()

    def test_unapplied_change_or_changed_sale_fails_verification(self):
        for enabled, sale in ((True, 21000), (False, 22000)):
            self.query.return_value = {'auto_enabled': enabled, 'sale': sale}
            self.assertEqual(self.run_action()['status'], 'unverified')


class PriceModeTests(unittest.TestCase):
    def test_automatic_mode_requires_both_bounds(self):
        from rocket_price import price_target
        for low, high in [(None,None),(None,24000),(21000,None),(20000,20000),(0,22000)]:
            with self.subTest(low=low,high=high), self.assertRaises(ValueError):
                price_target({'auto_enabled':True}, 22000, low, high, 'set')

    def test_automatic_verification_requires_both_bounds_and_mode(self):
        from rocket_price import price_target, matches_target
        target=price_target({'auto_enabled':True}, None, 21000, 23000, 'set')
        self.assertTrue(matches_target(dict(target,sale=21500),target))
        self.assertFalse(matches_target(dict(target,maximum=24000),target))
        self.assertFalse(matches_target(dict(target,auto_enabled=False),target))

    def test_fixed_price_accepts_raise_and_rejects_bounds(self):
        from rocket_price import price_target, matches_target
        state={'auto_enabled':False,'sale':21000}
        target=price_target(state, 22000, None, None, 'set')
        self.assertEqual(target,dict(state,sale=22000))
        self.assertFalse(matches_target(dict(target,auto_enabled=True),target))
        with self.assertRaises(ValueError):
            price_target(state,22000,19000,22000,'set')
