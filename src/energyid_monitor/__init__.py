"""
EnergyID Monitor — report Anker SOLIX Solarbank Max AC data to EnergyID.

This package:
- Reads the battery over local Modbus TCP (no Anker cloud, no Home Assistant)
- Authenticates with EnergyID incoming webhooks
- Caches bearer tokens in SQLite
- Posts cumulative energy and SoC to EnergyID
"""

__version__ = "0.1.0"

from . import battery, common, energyid, logging_config, token_store

__all__ = [
    "battery",
    "common",
    "energyid",
    "logging_config",
    "token_store",
]
