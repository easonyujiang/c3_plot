"""
C3-Mass Dashboard — Server Configuration
========================================
"""

import os
from .paths import native_path

# Paths (all relative to project root)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROCKETS_DIR = os.environ.get('C3_MASS_ROCKETS_DIR', os.path.join(BASE_DIR, 'rockets'))
OUTPUT_DIR = os.environ.get('C3_MASS_OUTPUT_DIR', os.path.join(BASE_DIR, 'output'))
DB_PATH = os.environ.get('C3_MASS_DB_PATH', os.path.join(BASE_DIR, 'c3_plot.db'))
MIGRATIONS_DIR = os.path.join(BASE_DIR, 'migrations')

# Server
PORT = 8765
HOST = 'localhost'

# Phase 6B.1: default candidate paths for the user's KSP installation.
# The install-index module never scans disks or Steam libraries on its own:
# callers pass ksp_root explicitly, these candidates only serve as
# convenient defaults for interactive callers.
KSP_INSTALL_CANDIDATES = (
    native_path(r'D:\SteamLibrary\steamapps\common\Kerbal Space Program'),
)

# Orbital mechanics (RSS Earth, Cape Canaveral equivalent)
MU = 3.986004418e+14
R_EARTH = 6371000.0
G0 = 9.80665
SID_DAY = 86164.1
LAT = 19.81
ALT = 92.7
H_PARK = 220.0

# Standard missions are user-created; no pre-seeded sample records.
