"""TradeFlow 500-Item Ground-Truth OOD Benchmark Dataset."""
import json
import os
from typing import List, Dict, Any

_json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'benchmark_ood_500.json')
with open(_json_path, 'r', encoding='utf-8') as _f:
    OOD_500_DATASET: List[Dict[str, Any]] = json.load(_f)
