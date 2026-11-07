"""Runtime assembly rejects drift before removing any vendor payload."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stage_vendor_runtime',ROOT/'scripts/stage_vendor_runtime.py')
owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)


@pytest.fixture
def staged(tmp_path):
    root=tmp_path/'runtime';root.mkdir()
    contract_path=ROOT/'deploy/vendor-runtime.json';contract=json.loads(contract_path.read_bytes())
    blocks=[]
    for row in contract['packages']['x86_64']:
        block='\n'.join(k+':'+v for k,v in row.items())
        if row['P']=='sqlite-libs':block+='\nF:usr/lib\nR:libsqlite3.so.0'
        if row['P']=='py3-pip-wheel':block+='\nF:usr/share/python-wheels\nR:pip.whl'
        blocks.append(block)
    db=root/'lib/apk/db/installed';db.parent.mkdir(parents=True);db.write_text('\n\n'.join(blocks)+'\n\n')
    for name in ['usr/lib/libsqlite3.so.0','usr/share/python-wheels/pip.whl','usr/lib/python3.14/sqlite3/__main__.py','usr/lib/python3.14/sqlite3/__init__.py','usr/lib/python3.14/ensurepip/__init__.py','usr/lib/python3.14/lib-dynload/_sqlite3.test.so']:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic vendor file')
    sbom=root/f"var/lib/db/sbom/zlib-{contract['zlib_package']}.spdx.json";sbom.parent.mkdir(parents=True);sbom.write_text(json.dumps({'packages':[{'downloadLocation':f"https://github.com/madler/zlib/archive/{contract['zlib_source']}.tar.gz"}]}))
    return root,db,contract,contract_path,sbom


def test_helper_removes_actual_sqlite_and_bootstrap_payload_and_records(staged):
    root,db,contract,path,_=staged
    owner.stage(root,'helper',contract['base_digest'],contract_path=path)
    assert not (root/'usr/lib/libsqlite3.so.0').exists()
    assert not (root/'usr/share/python-wheels/pip.whl').exists()
    assert not (root/'usr/lib/python3.14/sqlite3').exists()
    assert not list((root/'usr/lib/python3.14/lib-dynload').glob('_sqlite3*'))
    assert not {'sqlite-libs','py3-pip-wheel'} & owner.records(db.read_text()).keys()
    evidence=json.loads((root/'usr/share/breadmaker/vendor-runtime/assembly.json').read_text())
    assert evidence['original_packages']==contract['packages']['x86_64']
    assert evidence['contract_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('change',['version','checksum','architecture','source','unknown','missing'])
def test_package_drift_fails_before_removing_payload(staged,change):
    root,db,contract,path,_=staged;rows=owner.records(db.read_text())
    if change=='missing':rows.pop('zlib')
    elif change=='unknown':rows['unexpected']=({'P':'unexpected'},'P:unexpected\nV:1-r0')
    else:
        fields,block=rows['zlib'];key={'version':'V','checksum':'C','architecture':'A','source':'c'}[change];block=block.replace(key+':'+fields[key],key+':changed');rows['zlib']=(fields,block)
    db.write_text('\n\n'.join(block for _,block in rows.values()))
    with pytest.raises(ValueError,match='GRAPH_MISMATCH'):owner.stage(root,'helper',contract['base_digest'],contract_path=path)
    assert (root/'usr/lib/libsqlite3.so.0').read_bytes()==b'synthetic vendor file'


@pytest.mark.parametrize('bad',['base','source','traversal','symlink','mode'])
def test_untrusted_source_or_removal_path_is_rejected(staged,tmp_path,bad):
    root,db,contract,path,sbom=staged;digest=contract['base_digest'];mode='helper'
    outside=tmp_path/'outside';outside.write_bytes(b'preserve')
    if bad=='base':digest='0'*64
    elif bad=='source':sbom.write_text('{"packages":[]}')
    elif bad=='traversal':db.write_text(db.read_text().replace('F:usr/lib\nR:libsqlite3.so.0','F:../../outside\nR:other'))
    elif bad=='symlink':(root/'usr/lib/libsqlite3.so.0').unlink();(root/'usr/lib/libsqlite3.so.0').symlink_to(outside)
    else:mode='unknown'
    with pytest.raises(ValueError):owner.stage(root,mode,digest,contract_path=path)
    assert outside.read_bytes()==b'preserve'


def test_hosted_core_is_distinct_from_removed_vendor_sqlite(staged,tmp_path):
    root,db,contract,path,_=staged;lib=tmp_path/'core.so';lib.write_bytes(b'synthetic fixed core')
    evidence=tmp_path/'proof';evidence.mkdir();(evidence/'provenance.json').write_text(json.dumps({'version':'3.54.1','library_sha256':hashlib.sha256(lib.read_bytes()).hexdigest()}))
    owner.stage(root,'hosted',contract['base_digest'],contract_path=path,sqlite_root=evidence,sqlite_library=lib)
    assert (root/'opt/python-libs/libsqlite3.so.0').read_bytes()==lib.read_bytes()
    assert not (root/'usr/lib/libsqlite3.so.0').exists()
    assert (root/'usr/lib/python3.14/sqlite3/__init__.py').exists()
    assert not (root/'usr/lib/python3.14/sqlite3/__main__.py').exists()
    rows=owner.records(db.read_text());assert 'sqlite-libs' not in rows
    assert rows['breadmaker-sqlite-core'][0]=={'P':'breadmaker-sqlite-core','V':'3.54.1-r0','A':'x86_64','o':'sqlite'}
    assert 'Breadmaker release engineering' in rows['breadmaker-sqlite-core'][1]


def test_install_cannot_target_a_populated_directory_or_root(staged,monkeypatch):
    root,_,contract,_,_=staged
    monkeypatch.setattr(owner.subprocess,'run',lambda *a,**k:pytest.fail('No installer may run'))
    for unsafe in [root,Path('/')]:
        with pytest.raises(ValueError,match='MUST_BE_EMPTY'):owner.install(unsafe,contract)
    assert (root/'usr/lib/libsqlite3.so.0').read_bytes()==b'synthetic vendor file'


def test_install_pins_entire_reviewed_graph(tmp_path, monkeypatch):
    contract=json.loads((ROOT/'deploy/vendor-runtime.json').read_bytes())
    monkeypatch.setattr(owner.shutil,'copytree',lambda *a,**k:None)
    monkeypatch.setattr(owner.shutil,'copyfile',lambda *a,**k:None)
    calls=[]
    monkeypatch.setattr(owner.subprocess,'run',lambda *a,**k:calls.append((a,k)))
    root=tmp_path/'runtime'
    owner.install(root,contract)
    assert calls==[((['apk','--root',str(root),'--initdb','--no-scripts','add','--no-cache',
                     *[row['P']+'='+row['V'] for row in sorted(contract['packages']['x86_64'],key=lambda row:row['P'])]],),
                   {'check':True})]
    # Regression: these transitive packages previously floated to 2.44-r14.
    for name in ('glibc-2.44','glibc-2.44-locale-posix','ld-linux-2.44'):
        assert name+'=2.44-r13' in calls[0][0][0]
