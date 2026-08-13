import asyncio
import hashlib
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from PIL import Image

from app.batches import prepare_batch_preflight
from app.config import PROJECT_ROOT
from evals.demo_bundle import build_bundle, write_bundle
from evals.manifest import DemoScenario, load_manifest_v2

COMMITTED_MANIFEST = PROJECT_ROOT / "fixtures" / "reviewer-demo-v1.json"
COMMITTED_BUNDLE = PROJECT_ROOT / "frontend" / "public" / "demo"


def _upload(path: Path) -> UploadFile:
    content = path.read_bytes()
    return UploadFile(file=BytesIO(content), filename=path.name, size=len(content))


def _all_issue_codes(preflight) -> set[str]:
    return {
        issue.code.value
        for issue in [
            *preflight.issues,
            *(issue for case in preflight.cases for issue in case.issues),
            *(issue for image in preflight.images for issue in image.issues),
        ]
    }


def test_committed_demo_manifest_and_files_match_deterministic_source(tmp_path: Path) -> None:
    generated_manifest = tmp_path / "reviewer-demo-v1.json"
    generated_bundle = tmp_path / "demo"

    write_bundle(generated_manifest, generated_bundle)

    assert generated_manifest.read_bytes() == COMMITTED_MANIFEST.read_bytes()
    assert {
        path.relative_to(generated_bundle): path.read_bytes()
        for path in generated_bundle.rglob("*")
        if path.is_file()
    } == {
        path.relative_to(COMMITTED_BUNDLE): path.read_bytes()
        for path in COMMITTED_BUNDLE.rglob("*")
        if path.is_file()
    }


def test_every_committed_demo_artifact_matches_its_manifest_hash() -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)

    assert manifest.owner.value == "demo_bundle"
    assert len(manifest.cases) == 6
    for case in manifest.cases:
        assert case.reviewer_demo is not None
        directory = COMMITTED_BUNDLE / case.reviewer_demo.directory
        for artifact in case.artifacts:
            path = directory / artifact.filename
            assert path.is_file(), path
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256


def test_p0_demo_images_are_valid_and_have_unambiguous_expected_inputs() -> None:
    manifest = load_manifest_v2(COMMITTED_MANIFEST)
    p0_cases = [
        case for case in manifest.cases if case.reviewer_demo.workflow.value == "p0_single_review"
    ]

    assert {case.reviewer_demo.scenario for case in p0_cases} == {
        DemoScenario.MATCHING_LABEL,
        DemoScenario.MATERIAL_MISMATCH,
        DemoScenario.UNREADABLE_LABEL,
    }
    for case in p0_cases:
        assert case.expected_application is not None
        assert case.expected_application.brand_name == "OLD TOM"
        assert case.expected_application.class_type == "Kentucky Straight Bourbon Whiskey"
        assert str(case.expected_application.abv) == "45"
        assert str(case.expected_application.net_contents.value) == "750"
        assert case.expected_application.net_contents.unit.value == "mL"
        image_path = COMMITTED_BUNDLE / case.reviewer_demo.directory / case.artifacts[0].filename
        with Image.open(image_path) as image:
            image.verify()


def test_demo_batch_packages_reproduce_documented_preflight(tmp_path: Path) -> None:
    manifest, _ = build_bundle()
    package_cases = [
        case
        for case in manifest.cases
        if case.reviewer_demo.scenario in {DemoScenario.VALID_BATCH, DemoScenario.MIXED_PREFLIGHT}
    ]

    async def run() -> None:
        for case in package_cases:
            assert case.reviewer_demo is not None
            assert case.expected_preflight is not None
            directory = COMMITTED_BUNDLE / case.reviewer_demo.directory
            spreadsheet = directory / "applications.csv"
            images = sorted(directory.glob("*.png"))
            async with prepare_batch_preflight(
                _upload(spreadsheet),
                [_upload(image) for image in images],
                temp_dir=tmp_path / case.id,
            ) as preflight:
                expected = case.expected_preflight
                assert preflight.ready_case_count == expected.ready_case_count
                assert preflight.correction_case_count == expected.correction_case_count
                assert _all_issue_codes(preflight) == {code.value for code in expected.issue_codes}

    asyncio.run(run())
