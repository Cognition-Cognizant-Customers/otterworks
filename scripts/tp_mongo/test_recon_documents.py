from documents_model import VERSION_ARRAY_BOUND
from recon_documents import classify_version_sequence


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
