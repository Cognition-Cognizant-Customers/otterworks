import pytest
from documents_model import VERSION_ARRAY_BOUND
from recon_documents import classify_version_sequence, validate_output_path


def test_recon_classifies_oversized_declared_gap_as_expected_quarantine():
    missing, quarantine = classify_version_sequence(
        VERSION_ARRAY_BOUND + 2,
        [],
    )

    assert missing is None
    assert quarantine == {
        "declared": VERSION_ARRAY_BOUND + 2,
        "missing_count": VERSION_ARRAY_BOUND + 2,
    }


def test_no_rerun_allows_partial_output_path():
    validate_output_path("build/tp-recon/mongo_documents.demo.partial.json", False)


def test_no_rerun_rejects_schema_report_output_path():
    with pytest.raises(ValueError, match="--rerun-migration"):
        validate_output_path("docs/tech-partnerships/recon/mongo_documents.live.recon.json", False)


def test_rerun_allows_schema_report_output_path():
    validate_output_path("docs/tech-partnerships/recon/mongo_documents.live.recon.json", True)
