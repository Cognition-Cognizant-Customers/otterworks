"""Shared configuration for the Single Pane of Glass demo.

All values can be overridden with environment variables so the same workflow
runs unchanged locally, in CI, and inside a scheduled Devin session.
"""
import os

# --- System 1: OtterWorks enterprise drive (structured "DB2 / mainframe" analog)
GATEWAY_URL = os.environ.get("OTTER_GATEWAY_URL", "http://localhost:8080")

# --- System 2: OtterWorks web portal (UI-only app, retrieved via the browser)
WEB_URL = os.environ.get("OTTER_WEB_URL", "http://localhost:3000")
# Chrome DevTools Protocol endpoint of the desktop browser Devin drives.
CDP_URL = os.environ.get("OTTER_CDP_URL", "http://localhost:29229")

# Credentials come from the Devin secrets vault (never hard-coded).
DRIVE_EMAIL = os.environ.get("DRIVE_EMAIL", "")
DRIVE_PASSWORD = os.environ.get("DRIVE_PASSWORD", "")

# --- System 3: external public web (World Bank Open Data — no auth)
# Macro indicators that give the enterprise drive external market context.
WORLDBANK_BASE = os.environ.get(
    "WORLDBANK_BASE", "https://api.worldbank.org/v2"
)
WORLDBANK_COUNTRY = os.environ.get("WORLDBANK_COUNTRY", "US")
WORLDBANK_INDICATORS = [
    ("FP.CPI.TOTL.ZG", "Inflation (CPI, annual %)", "%"),
    ("NY.GDP.MKTP.KD.ZG", "GDP growth (annual %)", "%"),
    ("SL.UEM.TOTL.ZS", "Unemployment (% labor force)", "%"),
]

# Where generated artifacts land.
OUTPUT_DIR = os.environ.get(
    "SPOG_OUTPUT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"),
)
