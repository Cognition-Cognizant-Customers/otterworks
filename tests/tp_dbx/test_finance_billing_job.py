import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


def load_job_module():
    job_source = Path(__file__).resolve().parents[2] / "scripts/tp_dbx/notebooks/finance_billing_job.py"
    spec = importlib.util.spec_from_file_location("finance_billing_job", job_source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


JOB = load_job_module()


def test_render_export_matches_legacy_layout():
    rows = [
        ("USD", "02", 2, -5),
        ("EUR", "01", 1, 10155441),
        ("USD", "01", 3, 5),
    ]

    expected = (
        b"Currency,RecordType,RecordCount,TotalAmount\n"
        b"EUR,INVOICE,1,101554.41\n"
        b"USD,INVOICE,3,0.05\n"
        b"USD,CREDIT,2,-0.05\n"
    )

    payload = JOB.render_export(rows)

    assert payload == expected
    assert not payload.startswith(b"\xef\xbb\xbf")


def test_render_export_rejects_unmapped_record_type():
    with pytest.raises(ValueError, match="unmapped record_type"):
        JOB.render_export([("USD", "99", 1, 100)])


@pytest.mark.parametrize(
    ("cents", "expected"),
    [(0, "0.00"), (5, "0.05"), (10155441, "101554.41"), (-5, "-0.05")],
)
def test_cents_to_amount(cents, expected):
    assert JOB.cents_to_amount(cents) == expected


def test_check_export_name_accepts_csv():
    assert JOB.check_export_name("finance_billing.csv") == "finance_billing.csv"


def test_check_export_name_rejects_mislabelled_artifact():
    with pytest.raises(ValueError, match="mislabelled_artifact_type"):
        JOB.check_export_name("finance_billing.xls")


@pytest.mark.parametrize("name", ["reports/finance_billing.csv", ".finance_billing.csv"])
def test_check_export_name_rejects_non_bare_names(name):
    with pytest.raises(ValueError):
        JOB.check_export_name(name)


def test_export_row_count_excludes_header_and_tolerates_trailing_newline():
    payload = b"Currency,RecordType,RecordCount,TotalAmount\nUSD,INVOICE,1,1.00\nEUR,CREDIT,2,2.00\n"

    assert JOB.export_row_count(payload) == 2
    assert JOB.export_row_count(b"Currency,RecordType,RecordCount,TotalAmount\n") == 0
    assert JOB.export_row_count(b"Currency,RecordType,RecordCount,TotalAmount") == 0


def test_deliver_writes_and_verifies_payload(tmp_path):
    payload = JOB.render_export([("USD", "01", 1, 100)])
    directory = tmp_path / "exports"

    result = JOB.deliver(payload, str(directory), "finance_billing.csv")

    assert (directory / "finance_billing.csv").is_file()
    assert not (directory / ".finance_billing.csv.staging").exists()
    assert result == {
        "path": str(directory / "finance_billing.csv"),
        "byte_size": len(payload),
        "row_count": 1,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_deliver_skip_write_rejects_silent_noop(tmp_path):
    directory = tmp_path / "missing"

    with pytest.raises(JOB.DeliveryError, match="silent_delivery_noop"):
        JOB.deliver(b"payload", str(directory), "finance_billing.csv", skip_write=True)

    assert not (directory / "finance_billing.csv").exists()


def test_deliver_rejects_non_csv_before_writing(tmp_path):
    directory = tmp_path / "not-created"

    with pytest.raises(ValueError, match="mislabelled_artifact_type"):
        JOB.deliver(b"payload", str(directory), "finance_billing.xls")

    assert not directory.exists()


@pytest.mark.parametrize("value", ["Bad", "a" * 25, "x-y", ""])
def test_require_ns_rejects_bad_values(value):
    with pytest.raises(ValueError):
        JOB.require_ns(value)


@pytest.mark.parametrize(
    ("validator", "value"),
    [
        (JOB.require_ns, "cnvfinаnce"),
        (lambda value: JOB.require_ident(value, "catalog"), "ow_tp１"),
        (JOB.require_run_id, "run１"),
        (JOB.check_export_name, "finance_billing１.csv"),
    ],
)
def test_ascii_validators_reject_non_ascii_lookalikes(validator, value):
    with pytest.raises(ValueError):
        validator(value)


def test_ascii_validators_accept_real_values():
    assert JOB.require_ns("cnvfinance") == "cnvfinance"
    assert JOB.require_ident("ow_tp", "catalog") == "ow_tp"
    assert JOB.require_ident("parsed", "input_subdir") == "parsed"
    assert JOB.require_run_id("12345") == "12345"
    assert JOB.require_run_id("run-1_a") == "run-1_a"
    assert JOB.check_export_name("finance_billing.csv") == "finance_billing.csv"


@pytest.mark.parametrize("value", ["x-y", ""])
def test_require_ident_rejects_bad_values(value):
    with pytest.raises(ValueError):
        JOB.require_ident(value, "identifier")


def test_validation_query_rejects_null_raw_lines():
    query = JOB.validation_query(JOB.Names(ns="cnvfinance"))

    assert "WHERE raw_line IS NULL OR length(trim(raw_line)) > 0" in query
    assert "WHEN raw_line IS NULL THEN 'null_raw_line'" in query
    assert "WHERE raw_line IS NULL\n" in query


def test_names_and_ddl_targets_are_namespace_suffixed():
    names = JOB.Names(catalog="ow_tp", ns="cnvfinance")
    targets = [names.bronze, names.silver, names.gold, names.audit]

    assert all(target.endswith("_cnvfinance") for target in targets)
    for statement, target in zip(JOB.ddl(names), targets):
        assert target in statement


@pytest.mark.parametrize("value", ["12345", "run-1_a"])
def test_require_run_id_accepts_platform_run_ids(value):
    assert JOB.require_run_id(value) == value


@pytest.mark.parametrize("value", ["1'); DROP TABLE t; --", "", "run id"])
def test_require_run_id_rejects_values_that_would_escape_the_sql_literal(value):
    with pytest.raises(ValueError):
        JOB.require_run_id(value)


def test_deliver_skip_write_preserves_stale_target_before_verification(tmp_path):
    payload = JOB.render_export([("USD", "01", 1, 100)])
    directory = tmp_path / "exports"
    directory.mkdir()
    target = directory / "finance_billing.csv"
    target.write_bytes(payload)

    with pytest.raises(JOB.DeliveryError, match="silent_delivery_noop"):
        JOB.deliver(payload, str(directory), target.name, skip_write=True)

    assert target.read_bytes() == payload
    assert not (directory / ".finance_billing.csv.staging").exists()


def test_deliver_scratch_mismatch_preserves_prior_export(tmp_path, monkeypatch):
    prior_payload = JOB.render_export([("USD", "01", 1, 100)])
    new_payload = JOB.render_export([("USD", "01", 2, 200)])
    directory = tmp_path / "exports"
    directory.mkdir()
    target = directory / "finance_billing.csv"
    target.write_bytes(prior_payload)
    real_open = open

    def corrupt_staging(path, mode="r", *args, **kwargs):
        if mode == "rb" and str(path).endswith(".staging"):
            with real_open(path, "wb") as handle:
                handle.write(b"corrupted staging bytes")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(JOB, "open", corrupt_staging, raising=False)

    with pytest.raises(JOB.DeliveryError, match="delivery mismatch"):
        JOB.deliver(new_payload, str(directory), target.name)

    assert target.read_bytes() == prior_payload
    assert not (directory / ".finance_billing.csv.staging").exists()


@pytest.mark.parametrize(
    "name",
    ["finance'billing.csv", "finance billing.csv", "finance,billing.csv", "finance@billing.csv"],
)
def test_check_export_name_rejects_characters_outside_allowed_alphabet(name):
    with pytest.raises(ValueError):
        JOB.check_export_name(name)


def test_batch_files_lists_nested_and_unfiltered_files(tmp_path):
    input_dir = tmp_path / "parsed"
    (input_dir / "sub").mkdir(parents=True)
    for relative in (
        "CUSTBILL_CNVFINANCE_001.psv",
        "CUSTBILL_CNVFINANCE_002.psv",
        "notes.txt",
        "sub/CUSTBILL_X.psv",
    ):
        (input_dir / relative).write_text("payload")

    assert JOB.batch_files(str(input_dir)) == [
        "CUSTBILL_CNVFINANCE_001.psv",
        "CUSTBILL_CNVFINANCE_002.psv",
        "notes.txt",
        "sub/CUSTBILL_X.psv",
    ]


def test_require_input_dir_rejects_missing_directory(tmp_path):
    with pytest.raises(ValueError, match="missing_input_dir"):
        JOB.require_input_dir(str(tmp_path / "missing"))


def test_require_input_dir_accepts_existing_empty_directory(tmp_path):
    input_dir = tmp_path / "parsed_empty"
    input_dir.mkdir()

    assert JOB.require_input_dir(str(input_dir)) == str(input_dir)
    assert JOB.batch_files(str(input_dir)) == []


def test_unrecognised_inputs_rejects_non_matching_batch_files():
    files = [
        "CUSTBILL_CNVFINANCE_001.psv",
        "CUSTBILL_CNVFINANCE_002.psv",
        "CUSTBILL_CNVFINANCE_003.PSV",
        "CUSTBILL_CNVFINANCE_004.psv.gz",
        "notes.txt",
        "sub/CUSTBILL_X.psv",
    ]

    assert JOB.unrecognised_inputs(files) == [
        "CUSTBILL_CNVFINANCE_003.PSV",
        "CUSTBILL_CNVFINANCE_004.psv.gz",
        "notes.txt",
        "sub/CUSTBILL_X.psv",
    ]
