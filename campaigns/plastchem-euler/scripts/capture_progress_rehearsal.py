"""Capture an early report cohort without changing the scheduled owner freeze."""
import argparse
import datetime as dt
from pathlib import Path
import freeze_progress_20260914 as capture

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monitor-pid', type=int, required=True)
    args = parser.parse_args()
    capture.DEST = Path('/mnt/r/plastchem-euler/progress-2026-09-14/rehearsal/freeze')
    capture.CUTOFF = dt.datetime.now(dt.timezone.utc)
    capture.freeze(args.monitor_pid, receipt_name='rehearsal-freeze-receipt.json')
