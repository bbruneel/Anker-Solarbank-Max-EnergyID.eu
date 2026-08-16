"""Live Modbus connectivity test for Anker SOLIX Solarbank Max AC.

Usage (from the project root, after `uv sync --extra dev`):

    ENERGYID_CONSOLE_LOGGING=true uv run python scripts/test_battery.py

Or:

    ENERGYID_CONSOLE_LOGGING=true uv run python -m energyid_monitor.battery
"""

from energyid_monitor.battery import main

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
