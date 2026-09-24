"""Compatibility entry point for the reconciled, last-priority nitro recovery.

Original outcomes and seven salvaged inputs are already verified. The replacement
waits for A-9 A/B, submits held, archives/registers originals, and lets the shared
controller release last. It never reruns the old unbounded preparation loop.
"""
import runpy,sys
from pathlib import Path
script=Path(__file__).with_name('recover_nitro_after_phase9.py')
sys.argv=[str(script),'--watch']
runpy.run_path(str(script),run_name='__main__')
