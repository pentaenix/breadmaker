"""Guard M12D chapter-replacement hybrid deconstruction.

The operator's 10.5.10 verifier found seven runtime failures with one NameError:
`encoded` (and subsequently `structured`) were used without unpacking `hybrid`.
This narrow source contract complements the existing behavioral tests in
`test_v10364.py` and `test_v10510_private_block_domain.py`.
The human operator, not the agent, runs this test through `verify-release`.
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v10511_atomic_chapter_replacement_binds_hybrid_before_publication():
    tree = ast.parse((ROOT / 'src/hosted/chapter_mutations.py').read_text(encoding='utf-8'))
    replace = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == 'replace_chapter_blocks'
    )
    branches = [
        node for node in ast.walk(replace)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name) and node.test.left.id == 'hybrid'
        and len(node.test.ops) == 1 and isinstance(node.test.ops[0], ast.IsNot)
        and len(node.test.comparators) == 1
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value is None
    ]
    assert len(branches) == 1
    branch = branches[0]
    first = branch.body[0]
    assert isinstance(first, ast.Assign) and len(first.targets) == 1
    target = first.targets[0]
    assert isinstance(target, ast.Tuple)
    assert [item.id for item in target.elts if isinstance(item, ast.Name)] == ['structured', 'encoded']
    assert isinstance(first.value, ast.Name) and first.value.id == 'hybrid'
    publication = branch.body[1]
    assert isinstance(publication, ast.Assign)
    assert isinstance(publication.value, ast.Call)
    assert isinstance(publication.value.func, ast.Attribute)
    assert publication.value.func.attr == '_publish_hybrid'
    # Verify publication uses the unpacked object bytes, not the untyped container.
    assert any(isinstance(arg, ast.Name) and arg.id == 'encoded' for arg in publication.value.args)
