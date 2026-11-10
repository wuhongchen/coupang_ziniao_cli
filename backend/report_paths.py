"""Small shared primitives, independent of the seller workbench UI."""
from pathlib import Path
import math

ROOT=Path(__file__).resolve().parents[1]

def number(value):
    if value is None or value in ('','-','데이터 없음'):
        return None
    try:
        result=float(str(value).replace(',',''))
        return result if math.isfinite(result) else None
    except (ValueError,TypeError):
        return None
