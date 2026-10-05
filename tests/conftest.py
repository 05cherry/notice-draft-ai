"""pytest 공통 설정.

`pythonpath = src`(pytest.ini)가 import 경로를 잡아 주지만, 테스트를 pytest 없이
직접 돌릴 때도 되도록 여기서 한 번 더 넣는다.

여기 있는 검사는 **전부 오프라인**이다. OpenSearch·OpenAI·빗썸에 접속하지 않으므로
자격증명 없이, 다른 PC에서도 그대로 돈다. 바깥이 필요한 검사는 `try_*.py`(수동)로 둔다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
