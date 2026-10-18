import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from hearth.valuation import verify
from hearth.valuation.eodhd import EodhdSource
from hearth.valuation.verify import Mismatch, compare, read_reference
from tests.valuation.conftest import quote

MON, TUE = date(2026, 10, 19), date(2026, 10, 20)


def test_prices_within_half_a_cent_pass() -> None:
    result = compare(
        [quote(as_of=MON, price="101.2549"), quote(as_of=TUE, price="101.995")],
        {("FUNDA", MON): Decimal("101.25"), ("FUNDA", TUE): Decimal("101.99")},
    )

    assert result.passed
    assert result.matched == 2


def test_a_price_more_than_half_a_cent_off_fails() -> None:
    result = compare([quote(as_of=MON, price="101.2551")], {("FUNDA", MON): Decimal("101.25")})

    assert not result.passed
    assert result.mismatched == (Mismatch("FUNDA", MON, Decimal("101.2551"), Decimal("101.25")),)
    assert result.mismatched[0].difference == Decimal("0.0051")
    assert result.matched == 0


def test_a_day_missing_on_either_side_fails() -> None:
    result = compare(
        [quote(as_of=TUE)],
        {("FUNDA", MON): Decimal("101.25")},
    )

    assert not result.passed
    assert result.missing_from_source == (("FUNDA", MON),)
    assert result.missing_from_reference == (("FUNDA", TUE),)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "published.csv"
    path.write_text(text)
    return path


def test_published_navs_are_read_exactly(tmp_path: Path) -> None:
    path = write(tmp_path, "symbol,date,nav\nFUNDA,2026-10-19,101.25\nFUNDB,2026-10-19, 9.10\n")

    assert read_reference(path) == {
        ("FUNDA", MON): Decimal("101.25"),
        ("FUNDB", MON): Decimal("9.10"),
    }


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("ticker,date,nav\n", "header"),
        ("symbol,date,nav\nFUNDA,19/10/2026,1\n", ":2: expected"),
        ("symbol,date,nav\nFUNDA,2026-10-19,abc\n", ":2: expected"),
        ("symbol,date,nav\nFUNDA,2026-10-19\n", ":2: expected"),
        ("symbol,date,nav\nFUNDA,2026-10-19,0\n", "positive"),
        ("symbol,date,nav\n,2026-10-19,1\n", "positive"),
        ("symbol,date,nav\nFUNDA,2026-10-19,1\nFUNDA,2026-10-19,1\n", ":3: FUNDA on 2026-10-19"),
    ],
)
def test_a_malformed_reference_is_refused(tmp_path: Path, text: str, complaint: str) -> None:
    with pytest.raises(ValueError, match=complaint):
        read_reference(write(tmp_path, text))


def test_without_a_key_verification_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(verify.API_KEY_VARIABLE, raising=False)

    assert verify.main([str(write(tmp_path, "symbol,date,nav\n"))]) == 2
    assert "no EODHD API key is configured" in capsys.readouterr().err


@pytest.mark.parametrize(("close", "status", "verdict"), [(101.25, 0, "PASS"), (101.26, 1, "FAIL")])
def test_verification_fetches_each_symbol_over_its_days_and_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    close: float,
    status: int,
    verdict: str,
) -> None:
    urls: list[str] = []

    def transport(url: str) -> tuple[int, bytes]:
        urls.append(url)
        rows = [{"date": day, "close": close} for day in ("2026-10-19", "2026-10-20")]
        return 200, json.dumps(rows).encode()

    monkeypatch.setenv(verify.API_KEY_VARIABLE, "synthetic-key")
    monkeypatch.setattr(verify, "EodhdSource", lambda key: EodhdSource(key, transport=transport))
    published = write(
        tmp_path, "symbol,date,nav\nFUNDA,2026-10-19,101.25\nFUNDA,2026-10-20,101.25\n"
    )

    assert verify.main([str(published)]) == status
    out = capsys.readouterr().out
    assert out.splitlines()[-1].startswith(f"{verdict}:")
    (url,) = urls
    assert "FUNDA.US" in url
    assert "from=2026-10-19" in url
    assert "to=2026-10-20" in url
