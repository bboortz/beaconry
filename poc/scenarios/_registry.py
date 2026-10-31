"""Explicit scenario list.

Adding a scenario: subclass `Scenario`, give it a unique `order`, add one
entry below. No autodiscovery — grep-friendly and trivially filterable.
"""

from __future__ import annotations

from ._base import Scenario
from .s01_fresh_cve import FreshCveScenario
from .s02_fix_closes import FixClosesScenario
from .s03_regression import RegressionScenario
from .s04_snooze_flow import SnoozeFlowScenario
from .s05_kev_tier0 import KevTier0Scenario
from .s06_internet_facing_tier1 import InternetFacingTier1Scenario
from .s07_digest_content_hash import DigestContentHashScenario
from .s08_heartbeat_sweep import HeartbeatSweepScenario
from .s09_multi_host_isolation import MultiHostIsolationScenario
from .s10_last_seen_bump import LastSeenBumpScenario

SCENARIOS: list[Scenario] = [
    FreshCveScenario(),
    FixClosesScenario(),
    RegressionScenario(),
    SnoozeFlowScenario(),
    KevTier0Scenario(),
    InternetFacingTier1Scenario(),
    DigestContentHashScenario(),
    HeartbeatSweepScenario(),
    MultiHostIsolationScenario(),
    LastSeenBumpScenario(),
]
