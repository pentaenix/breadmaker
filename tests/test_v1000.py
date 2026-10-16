from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = value.strip().split(".")
    assert len(parts) == 3 and all(part.isdigit() for part in parts)
    return tuple(int(part) for part in parts)


def test_v1000_public_repo_identity_and_split_contract():
    assert _version_tuple((ROOT / "VERSION").read_text(encoding="utf-8")) >= (10, 0, 0)
    split = (ROOT / "docs" / "REPOSITORY_SPLIT.md").read_text(encoding="utf-8")
    assert "breadmaker/" in split
    assert "breadmaker-cloud/" in split
    assert "Local Breadmaker remains fully usable without a Breadmaker account" in split
