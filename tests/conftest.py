import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from fixtures import fresh_connection  # noqa: E402

EXAMPLES = ROOT / "examples"


@pytest.fixture
def duckdb_con():
    con = fresh_connection()
    yield con
    con.close()


@pytest.fixture
def model():
    return json.loads((EXAMPLES / "model.json").read_text())


@pytest.fixture
def contract_a():
    return json.loads((EXAMPLES / "contract_a.json").read_text())


@pytest.fixture
def contract_b():
    return json.loads((EXAMPLES / "contract_b.json").read_text())


@pytest.fixture
def contract_c():
    return json.loads((EXAMPLES / "contract_c.json").read_text())
