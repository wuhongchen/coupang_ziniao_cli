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
