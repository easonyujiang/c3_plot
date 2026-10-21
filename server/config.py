"""
C3-Mass Dashboard — Server Configuration
========================================
"""

import os

# Paths (all relative to project root)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROCKETS_DIR = os.environ.get('C3_MASS_ROCKETS_DIR', os.path.join(BASE_DIR, 'rockets'))
ENGINES_DIR = os.path.join(BASE_DIR, 'engines')
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
    r'D:\SteamLibrary\steamapps\common\Kerbal Space Program',
)

# Orbital mechanics (RSS Earth, Cape Canaveral equivalent)
MU = 3.986004418e+14
R_EARTH = 6371000.0
G0 = 9.80665
SID_DAY = 86164.1
LAT = 19.81
ALT = 92.7
H_PARK = 220.0

# Standard mission definitions (pre-seeded in DB)
STANDARD_MISSIONS = [
    {"label": "GTO (220×35786)",   "c3": -16.35, "dv": 2451, "group_name": "Earth Orbit",    "sort_order": 1},
    {"label": "TLI (Lunar)",       "c3": -2.01,  "dv": 3130, "group_name": "Earth Orbit",    "sort_order": 2},
    {"label": "Earth Escape",      "c3": 0,      "dv": 3221, "group_name": "Interplanetary", "sort_order": 3},
    {"label": "Venus Transfer",    "c3": 5.2,    "dv": 3455, "group_name": "Interplanetary", "sort_order": 4},
    {"label": "Mars Transfer",     "c3": 8.7,    "dv": 3610, "group_name": "Interplanetary", "sort_order": 5},
    {"label": "Asteroid Belt",     "c3": 15.0,   "dv": 3883, "group_name": "Interplanetary", "sort_order": 6},
    {"label": "Jupiter Transfer",  "c3": 55.0,   "dv": 5488, "group_name": "Outer Planets",  "sort_order": 7},
    {"label": "Saturn Transfer",   "c3": 50.0,   "dv": 5298, "group_name": "Outer Planets",  "sort_order": 8},
    {"label": "Mercury Transfer",  "c3": 60.0,   "dv": 5675, "group_name": "Outer Planets",  "sort_order": 9},
    {"label": "Uranus Transfer",   "c3": 80.0,   "dv": 6210, "group_name": "Outer Planets",  "sort_order": 10},
    {"label": "Neptune Transfer",  "c3": 90.0,   "dv": 6540, "group_name": "Outer Planets",  "sort_order": 11},
    {"label": "Pluto Transfer",    "c3": 100.0,  "dv": 6840, "group_name": "Outer Planets",  "sort_order": 12},
]
