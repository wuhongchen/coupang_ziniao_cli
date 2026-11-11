"""Human-readable CLI progress; stdout belongs to the JSON protocol."""
from datetime import datetime
import sys

from ziniao_environment import redact


def progress(message):
    timestamp = datetime.now().astimezone().strftime('%H:%M:%S')
    print(f'[{timestamp}] {redact(message)}', file=sys.stderr, flush=True)


def finish(label, status):
    descriptions = {
        'complete': '完成', 'complete_with_skips': '完成，部分步骤已跳过',
        'skipped_permission': '权限受限，已跳过', 'failed': '失败',
        'incomplete': '未完成', 'incomplete_data': '数据不完整',
        'awaiting_download': '等待下载', 'awaiting_save': '等待保存',
    }
    progress(f'{label} · {descriptions.get(status, status)}')
