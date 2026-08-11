from pathlib import Path

from PIL import Image

from evals.fixtures import load_manifest, render_fixture

MANIFEST = Path(__file__).resolve().parents[2] / "fixtures" / "live-evaluation-v1.json"


def test_manifest_loads_all_quality_gate_cases() -> None:
    manifest = load_manifest(MANIFEST)

    assert manifest.revision == "live-evaluation-v1"
    assert [case.id for case in manifest.cases] == [
        "clear-matching-label",
        "mismatched-net-contents",
        "altered-government-warning",
        "unreadable-label",
    ]
    assert manifest.cases[-1].requires_uncertainty is True


def test_fixture_renderer_produces_deterministic_normalized_pngs(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST)
    byte_counts: list[int] = []

    for case in manifest.cases:
        first = render_fixture(case, tmp_path / f"{case.id}-first.png")
        second = render_fixture(case, tmp_path / f"{case.id}-second.png")
        assert first.path.read_bytes() == second.path.read_bytes()
        with Image.open(first.path) as image:
            assert image.format == "PNG"
            assert image.size == (1_600, 1_200)
            assert image.info == {}
        byte_counts.append(first.byte_count)

    assert len(set(byte_counts)) > 1
