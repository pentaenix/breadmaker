"""Repeatable local narration checks; no owner projects or full-book endurance run."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime',action='store_true',help='Also check installed model dictionaries/frontends')
parser.add_argument('--browser',action='append',choices=['chromium','firefox','webkit'],default=[])
parser.add_argument('--real',action='store_true',help='Also synthesize short real-model samples; requires installed models')
args=parser.parse_args()

def run(command,timeout=120,env=None):
    print('+ '+' '.join(map(str,command)),flush=True)
    subprocess.run(list(map(str,command)),cwd=ROOT,env=env,check=True,timeout=timeout)

run([sys.executable,'-m','pytest','-q','tests/unit/test_narration_frontend.py',
     'tests/test_v10566_narration.py','tests/test_v10566_narration_dsp.py'])
if args.runtime:
    node=shutil.which('node')
    if not node:raise SystemExit('Node is required')
    for name in ['engine','languages']:run([node,ROOT/'tools/narration'/f'test-{name}.mjs'])
    run([sys.executable,ROOT/'tools/narration/test-pronunciation.py'])
for browser in args.browser:
    run([sys.executable,ROOT/'tools/narration/test-ui.py'],timeout=240,
        env={**os.environ,'BREADMAKER_NARRATION_BROWSER':browser})
if args.real:
    for script,options in [('smoke.py',[]),('smoke-punctuation.py',[]),('smoke-languages.py',['--editor'])]:
        run([sys.executable,ROOT/'tools/narration'/script,*options],timeout=300)
print('Narration checks passed. This is not a full release or full-book endurance signoff.',flush=True)
