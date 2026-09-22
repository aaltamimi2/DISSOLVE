import hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
current=hashlib.sha256((root/'CHARTER.txt').read_bytes()).hexdigest()
seen=(root/'state/CHARTER.last-read.sha256').read_text().split()[0]
if current!=seen:print('CHARTER_CHANGED: read and apply the new appended instructions before proceeding.')
