"""Shared live game location for integration tests; no local snapshot fallback."""
from pathlib import Path
from c3_mass.game_data import ksp_root


def configured_game_root():
    try:
        return ksp_root()
    except FileNotFoundError:
        # Existing skipUnless decorators treat this as unavailable.
        return Path(__file__).resolve().parents[1] / '.unconfigured-ksp'
