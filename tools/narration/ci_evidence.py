"""Small public runtime inventory/provenance; NEVER package model binaries."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
if __package__ in (None,''):sys.path.insert(0,str(ROOT/'src'))
HELPER_FILES={'deploy/Dockerfile.pronunciation','deploy/requirements-pronunciation.txt',
    'src/hosted/narration/pronunciation_helper.py','tools/narration/assert-helper-data.py',
    'tools/narration/language-data-pins.json','tools/narration/THIRD_PARTY_NOTICES.md'}


def helper_fingerprint(root=ROOT):
    paths=subprocess.check_output(['git','ls-files','src/narration'],cwd=root,text=True).splitlines()
    paths=sorted(set(paths)|HELPER_FILES)
    digest=hashlib.sha256()
    for path in paths:
        source=root/path
        if source.is_symlink() or not source.is_file():raise ValueError('NARRATION_SOURCE_INVALID')
        digest.update(path.encode()+b'\0'+hashlib.sha256(source.read_bytes()).digest())
    return digest.hexdigest()


def evidence(runtime,sha,base):
    if not re.fullmatch('[0-9a-f]{40}',sha) or not re.fullmatch('[0-9a-f]{64}',base):
        raise ValueError('NARRATION_PROVENANCE_INVALID')
    runtime=Path(runtime);files=[];size=0
    for path in sorted(runtime.rglob('*')):
        if path.is_symlink():raise ValueError('NARRATION_RUNTIME_SYMLINK')
        if not path.is_file():continue
        if path.suffix not in {'.js','.mjs','.json','.md'} and not path.name.endswith('-LICENSE'):
            raise ValueError('NARRATION_RUNTIME_BINARY_FORBIDDEN')
        raw=path.read_bytes();size+=len(raw)
        files.append(dict(path=path.relative_to(runtime).as_posix(),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    if not 20<=len(files)<=64 or not 0<size<=20*1024**2:raise ValueError('NARRATION_RUNTIME_INVENTORY_INVALID')
    from hosted.narration.models import PinnedModelCatalog
    catalog=PinnedModelCatalog(json.loads((runtime/'hosted-manifest.json').read_bytes()))
    return dict(v=1,editor_sha=sha,manifest=catalog.digest,helper_sources=helper_fingerprint(),
        helper_base=base,runtime=files,runtime_bytes=size)


def check_scan(report):
    if (not isinstance(report,dict) or report.get('SchemaVersion')!=2
            or report.get('ArtifactType')!='container_image' or not isinstance(report.get('Results'),list)):
        raise ValueError('NARRATION_SCAN_REPORT_INVALID')
    classes={(item.get('Class'),item.get('Type')) for item in report['Results'] if isinstance(item,dict)}
    if not {('os-pkgs','debian'),('lang-pkgs','python-pkg')}<=classes:
        raise ValueError('NARRATION_SCAN_COVERAGE_INCOMPLETE')
    for item in report['Results']:
        findings=item.get('Vulnerabilities') or []
        if not isinstance(findings,list):raise ValueError('NARRATION_SCAN_REPORT_INVALID')
        for finding in findings:
            if not isinstance(finding,dict) or finding.get('Severity') not in {'LOW','MEDIUM'}:
                raise ValueError('NARRATION_SCAN_BLOCKED')
    return {'os_and_python_scanned':True,'high_critical':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime');parser.add_argument('--sha')
    parser.add_argument('--base');parser.add_argument('--output');parser.add_argument('--check-scan')
    args=parser.parse_args()
    if args.check_scan:
        print(json.dumps(check_scan(json.loads(Path(args.check_scan).read_bytes()))));return
    if not all((args.runtime,args.sha,args.base,args.output)):parser.error('Provide runtime, sha, base and output, or check-scan')
    Path(args.output).write_text(json.dumps(evidence(args.runtime,args.sha,args.base),sort_keys=True))


if __name__=='__main__':main()
