"""Import the leader-rotation scenario without CARLA.

The scenario module imports `carla`, CARLA's `agents.navigation` package and the
PlatooningSimulator library (which itself imports carla) at module level. For
state-machine tests only the coordinator logic is exercised, so those modules are
replaced by MagicMock stubs in sys.modules. Nothing in this file is used by the
production path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenario" / "examples" / "leader_rotation_scenario.py"

_STUBBED = [
    "carla",
    "agents", "agents.navigation", "agents.navigation.controller",
    "agents.tools", "agents.tools.misc",
    "PlatooningSimulator", "PlatooningSimulator.Core",
    "PlatooningSimulator.PlatooningControllers", "PlatooningSimulator.ScenarioAgents",
]


def load_scenario_module(monkeypatch):
    for name in _STUBBED:
        monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    spec = importlib.util.spec_from_file_location("leader_rotation_scenario_under_test", SCENARIO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
