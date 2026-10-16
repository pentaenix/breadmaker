from __future__ import annotations

from pathlib import Path


def backend_source(root: Path | str) -> str:
    """Return the complete backend HTTP/domain source for historical source contracts.

    Pre-10.1 tests often assumed every endpoint lived in ``src/app.py``. 10.1 makes
    ``app.py`` the composition root, decomposes platform contracts across ``src/backend`` modules, and moves feature routes into ``src/api``.
    Historical tests should assert the contract across the backend boundary rather than
    force implementation back into one monolithic file.
    """
    root=Path(root)
    src=root/'src'
    paths=[src/'app.py',src/'backend'/'core.py']
    backend=src/'backend'
    # Platform contracts have been decomposed below core. Historical source tests
    # assert the backend boundary, not one physical module, so include every cohesive
    # backend service without forcing implementation back into core.py.
    if backend.is_dir():
        paths.extend(
            path for path in sorted(backend.glob('*.py'))
            if path.name not in {'core.py','__init__.py'}
        )
    api=src/'api'
    # Project/profile/workspace administration moved from backend.project_routes into
    # a platform HTTP package under api/. Historical backend source contracts include
    # that package without recreating the retired backend facade.
    project_management=api/'project_management'
    if project_management.is_dir():
        project_order=('profiles','workspace','metadata','destructive_actions','projects','members','packages','json_transfer','assets','health')
        paths.extend(project_management/f'{part}.py' for part in project_order)
    # Preserve the historical monolith's feature-definition order so old source-order
    # contracts keep testing semantics rather than alphabetical filenames.
    module_order=(
        'manuscript','annotations','comments','reviews','tasks','canvas','icon_markers',
        'maps','workspace_tools','find_replace','checkpoints','durability','import_export',
    )
    if api.is_dir():
        for name in module_order:
            module_file=api/f'{name}.py'
            module_package=api/name
            if module_file.is_file():
                paths.append(module_file)
            elif module_package.is_dir():
                package_order={
                    'manuscript':('navigation','document','images','chapter_structure','anchors','chapters','scenes'),
                    'reviews':('domain','snapshots','presets','portable','reader','questionnaires','feedback','participants','markers','lifecycle'),
                    'canvas':('populate','developmental','registry','versions','lifecycle','nodes','edges','structure'),
                    'tasks':('lifecycle','milestones','duplication'),
                    'maps':('domain','anchors','lifecycle','lines','items','junctions','layout','populate'),
                    'durability':('cuts','history','undo','history_baseline','recovery','backup_destinations','recovery_backups','backup_inventory','backups'),
                    'checkpoints':('capture','lineage','comparison','restore_support','restore','lifecycle','apply'),
                    'import_export':('docx_parser','uploads','docx_import','docx_export','pdf_export','assets'),
                    'workspace_tools':('references','bookmark','ui_state','documents'),
                    'annotations':('routes',),
                    'comments':('routes',),
                    'find_replace':('routes',),
                    'icon_markers':('routes',),
                }.get(name,())
                if package_order:
                    paths.extend(module_package/f'{part}.py' for part in package_order)
                else:
                    paths.extend(path for path in sorted(module_package.glob('*.py')) if path.name!='__init__.py')
    combined='\n\n'.join(p.read_text(encoding='utf-8') for p in paths if p.is_file())
    # Historical source contracts predate APIRouter extraction; normalize the decorator
    # owner so they keep asserting method/path semantics instead of the composition style.
    return combined.replace('@router.','@app.')


def composition_source(root: Path | str) -> str:
    """Return the tiny application assembly source without exposing path assumptions in tests."""
    root=Path(root)
    return (root/'src'/'app.py').read_text(encoding='utf-8')


def storage_source(root: Path | str) -> str:
    """Return the complete decomposed storage implementation for historical source contracts.

    Pre-10.2.13 tests assumed every persistence concern lived in one storage module.
    Storage is now decomposed by ownership; historical source tests assert the complete
    storage boundary without recreating a compatibility facade.
    """
    root=Path(root)
    storage=root/'src'/'storage'
    module_order=(
        'context','coordination','schema_contracts','values','invariants','database',
        'mutations','history','undo','assets','integrity','recovery','schema',
    )
    return '\n\n'.join(
        (storage/f'{name}.py').read_text(encoding='utf-8')
        for name in module_order if (storage/f'{name}.py').is_file()
    )


def reviews_source(root: Path | str) -> str:
    """Return the complete decomposed Reviews domain in ownership order."""
    root=Path(root)
    reviews=root/'src'/'api'/'reviews'
    order=('domain','snapshots','presets','portable','reader','questionnaires','feedback','participants','markers','lifecycle')
    return '\n\n'.join((reviews/f'{name}.py').read_text(encoding='utf-8') for name in order)


def manuscript_source(root: Path | str) -> str:
    """Return the complete decomposed Manuscript domain in historical ownership order."""
    root=Path(root)
    manuscript=root/'src'/'api'/'manuscript'
    order=('navigation','document','images','chapter_structure','anchors','chapters','scenes')
    return '\n\n'.join((manuscript/f'{name}.py').read_text(encoding='utf-8') for name in order)


def canvas_source(root: Path | str) -> str:
    """Return the complete decomposed Canvas domain in ownership order."""
    root=Path(root)
    canvas=root/'src'/'api'/'canvas'
    order=('populate','developmental','registry','versions','lifecycle','nodes','edges','structure')
    return '\n\n'.join((canvas/f'{name}.py').read_text(encoding='utf-8') for name in order)


def durability_source(root: Path | str) -> str:
    """Return the complete decomposed durability HTTP/domain source in route order."""
    root=Path(root)
    durability=root/'src'/'api'/'durability'
    order=('cuts','history','undo','history_baseline','recovery','backup_destinations','recovery_backups','backup_inventory','backups')
    return '\n\n'.join((durability/f'{name}.py').read_text(encoding='utf-8') for name in order)


def frontend_source(root: Path | str) -> str:
    """Return the complete browser JavaScript source in runtime load order.

    Historical frontend source contracts predate the 10.3 modular frontend. They
    assert behavior across the browser boundary rather than requiring every feature
    to remain in ``static/app.js``. The source order is derived from ``index.html``
    so the helper follows the same classic-script dependency order as the browser.
    """
    import re
    root=Path(root)
    html=(root/'static'/'index.html').read_text(encoding='utf-8')
    scripts=re.findall(r'<script[^>]+src=["\'](/static/[^"\'?]+\.js)(?:\?[^"\']*)?["\']',html)
    return ''.join(
        (root/path.lstrip('/')).read_text(encoding='utf-8')
        for path in scripts
        if (root/path.lstrip('/')).is_file()
    )


def frontend_source_from_app_path(app_path: Path | str) -> str:
    """Resolve the frontend boundary from a historical ``static/app.js`` path."""
    path=Path(app_path)
    if not path.is_absolute():
        path=(Path.cwd()/path).resolve()
    return frontend_source(path.parent.parent)


def frontend_remainder_source(root: Path | str) -> str:
    """Return the physical ``static/app.js`` startup shim.

    Historical 10.3.x architecture contracts use this helper to follow the
    shrinking file through the migration. Behavioral/source contracts should
    use :func:`frontend_source` instead.
    """
    root=Path(root)
    return (root/'static'/'app.js').read_text(encoding='utf-8')

def frontend_stylesheet_paths(root: Path | str) -> tuple[Path, ...]:
    """Return browser stylesheet files in the exact order declared by ``index.html``.

    CSS source order is part of the visual contract. 10.3.6 decomposes the historical
    monolith without regrouping rules; 10.3.7 keeps that boundary fixed while removing
    only mechanically provable redundant declarations; 10.3.8 adds the shared-component
    contract; and 10.3.9 freezes that boundary after the final JavaScript ownership cleanup.
    Tests inspect the same ordered stylesheet boundary that the browser receives.
    """
    import re
    root=Path(root)
    html=(root/'static'/'index.html').read_text(encoding='utf-8')
    hrefs=re.findall(r'<link[^>]+href=["\'](/static/[^"\'?]+\.css)(?:\?[^"\']*)?["\']',html)
    return tuple(root/href.lstrip('/') for href in hrefs if (root/href.lstrip('/')).is_file())


def frontend_styles_source(root: Path | str) -> str:
    """Return complete browser CSS in runtime cascade order."""
    return ''.join(path.read_text(encoding='utf-8') for path in frontend_stylesheet_paths(root))


def frontend_styles_source_from_styles_path(styles_path: Path | str) -> str:
    """Resolve the CSS boundary from a historical ``static/styles.css`` path."""
    path=Path(styles_path)
    if not path.is_absolute():
        path=(Path.cwd()/path).resolve()
    return frontend_styles_source(path.parent.parent)


def frontend_stylesheet_hrefs(root: Path | str) -> tuple[str, ...]:
    """Return versioned stylesheet hrefs in browser order for architecture contracts."""
    import re
    root=Path(root)
    html=(root/'static'/'index.html').read_text(encoding='utf-8')
    return tuple(re.findall(r'<link[^>]+href=["\'](/static/[^"\']+\.css(?:\?[^"\']*)?)["\']',html))

