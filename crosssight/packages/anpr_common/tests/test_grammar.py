"""Grammar unit tests — ≥40 cases covering formats, legacy codes, corrections."""

import pytest
from anpr_common.grammar import normalize_plate


@pytest.mark.parametrize(
    "raw,norm,fmt",
    [
        ("BR01AB1234", "BR01AB1234", "standard"),
        ("DL3CAB1234", "DL3CAB1234", "standard"),
        ("MH12DE1433", "MH12DE1433", "standard"),
        ("KA03MP1234", "KA03MP1234", "standard"),
        ("TN09AB0001", "TN09AB0001", "standard"),
        ("UP32JK4455", "UP32JK4455", "standard"),
        ("WB20A1234", "WB20A1234", "standard"),
        ("GJ1AB1234", "GJ1AB1234", "standard"),
        ("RJ14CD5678", "RJ14CD5678", "standard"),
        ("MP09XY9999", "MP09XY9999", "standard"),
        ("DL1CAA1111", "DL1CAA1111", "standard"),
        ("DL5SAB2222", "DL5SAB2222", "standard"),
        ("DL9CAC3333", "DL9CAC3333", "standard"),
        ("HR26DK8337", "HR26DK8337", "standard"),
        ("HR2DK8337", "HR2DK8337", "standard"),
        ("AP09BQ1234", "AP09BQ1234", "standard"),
        ("AP9BQ1234", "AP9BQ1234", "standard"),
        ("22BH1234AA", "22BH1234AA", "bh"),
        ("21BH0001A", "21BH0001A", "bh"),
        ("23BH9999ZZ", "23BH9999ZZ", "bh"),
        ("15BH1234B", "15BH1234B", "bh"),
        ("OR02AB1234", "OR02AB1234", "standard"),
        ("UA07CD5678", "UA07CD5678", "standard"),
        ("DN09XY1111", "DN09XY1111", "standard"),
        ("TS09AB1234", "TS09AB1234", "standard"),
        ("TG09AB1234", "TG09AB1234", "standard"),
        ("mh 12 de 1433", "MH12DE1433", "standard"),
        ("br01ab1234", "BR01AB1234", "standard"),
        ("  DL3CAB1234  ", "DL3CAB1234", "standard"),
        ("CG04AB1212", "CG04AB1212", "standard"),
        ("SK01A1001", "SK01A1001", "standard"),
        ("LA02AB3456", "LA02AB3456", "standard"),
    ],
)
def test_valid_plates(raw, norm, fmt):
    r = normalize_plate(raw)
    assert r.norm == norm
    assert r.valid is True
    assert r.format == fmt


@pytest.mark.parametrize(
    "raw,norm",
    [
        ("MH12D01433", "MH12DO1433"),
        ("KA03M51234", "KA03MS1234"),
        ("TN09A80001", "TN09AB0001"),
        ("UP32J64455", "UP32JG4455"),
        ("228H1234AA", "22BH1234AA"),
        ("HR26DK8B37", "HR26DK8837"),  # B→8 in serial digits
        ("DL3CAB1Z34", "DL3CAB1234"),  # Z→2 in serial
        ("GJ1AB12S4", "GJ1AB1254"),  # S→5 in serial
    ],
)
def test_confusion_corrections(raw, norm):
    r = normalize_plate(raw)
    assert r.valid is True
    assert r.norm == norm
    if raw.upper().replace(" ", "") != norm:
        assert len(r.corrections) >= 1


def test_already_valid_not_force_corrected():
    """Ambiguous but already-matching strings are left alone."""
    r = normalize_plate("WB2OA1234")  # WB + 2 + OA + 1234
    assert r.valid is True
    assert r.corrections == []
    assert r.norm == "WB2OA1234"


@pytest.mark.parametrize(
    "raw",
    [
        "XX99ZZ9999",
        "HELLO",
        "12345",
        "MH12",
        "BH22AA1234",
        "99BH123",
        "INVALIDPLATE",
        "QQ1AB1234",
        "ZZ99ABC1234",
        "",
        "MH12DE143",
        "NOTAPLATE",
        "ABCDEFGH",
    ],
)
def test_nonstandard_rejects(raw):
    r = normalize_plate(raw)
    assert r.valid is False
    assert r.format == "nonstandard"
    assert r.corrections == []


def test_overlong_may_correct_into_valid_series():
    """Extra digit in series can become a letter via confusion (length-preserving)."""
    r = normalize_plate("MH12DE14345")
    # length 11 → MH + 12 + DEI + 4345 after 1→I
    assert r.valid is True
    assert r.norm == "MH12DEI4345"


def test_never_force_correct_garbage():
    r = normalize_plate("NOTAPLATE")
    assert r.valid is False
    assert r.corrections == []


def test_bh_not_forced_to_standard():
    r = normalize_plate("22BH1234AA")
    assert r.format == "bh"
    assert r.valid is True


def test_result_fields():
    r = normalize_plate("mh12de1433")
    assert r.norm == "MH12DE1433"
    assert isinstance(r.corrections, list)
