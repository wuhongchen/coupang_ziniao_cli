"""Operator-selected timezone, otherwise the execution environment's local clock."""
from datetime import datetime
from zoneinfo import ZoneInfo
import os

def timezone():
 name=os.environ.get('WING_TIMEZONE')
 return ZoneInfo(name) if name else datetime.now().astimezone().tzinfo

def now():return datetime.now(timezone())

def timezone_name():return str(timezone())
