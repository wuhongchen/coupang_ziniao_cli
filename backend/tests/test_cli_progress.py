import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'operations-cli'))


class ProgressTests(unittest.TestCase):
    def test_progress_flushes_stderr_without_changing_stdout(self):
        from operation_progress import progress
        sink = Mock(wraps=io.StringIO())
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(sink):
            progress('正在下载')
        self.assertEqual(output.getvalue(), '')
        self.assertIn('正在下载', sink.getvalue())
        sink.flush.assert_called_once()

    def test_group_child_logs_inherit_stderr(self):
        from groups import execute
        with patch('groups.subprocess.run') as run:
            run.return_value.stdout = '{"status":"complete"}\n'
            run.return_value.returncode = 0
            self.assertEqual(execute(Path('.'), '123', ['analyze']), [{'status': 'complete'}])
        self.assertIsNone(run.call_args.kwargs.get('stderr'))
        self.assertFalse(run.call_args.kwargs.get('capture_output'))
