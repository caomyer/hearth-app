"""How the "nothing of anyone's" checks find a secret: as itself, never inside a random id.

The page crawl, the doorway's crawl and the run log test all look through
``household.appears``, so each kind of secret is planted here in the forms those outputs
write it, and must be found; and is put inside the random ids they also carry, and must not be.
"""

import pytest

from tests import household

blake = next(p for p in household.PLAID if p.username == "blake")


@pytest.mark.parametrize(
    ("text", "seen"),
    [
        # A page.
        (blake.checking_balance, f'<td class="num">{blake.checking_balance}</td>'),
        (blake.checking_balance, f"<td>-{blake.checking_balance}</td>"),
        ("1,618", "<p>Net worth $1,618</p>"),
        (f"&nbsp;{blake.checking_mask}", f"<span>Badger&nbsp;{blake.checking_mask}</span>"),
        (blake.checking, f"<a href='/accounts/x'>{blake.checking}</a>"),
        (blake.payee, f"<td>{blake.payee}</td>"),
        ("ZQAA", "<td>ZQAA</td>"),
        ("ZQAA", "/prices?symbol=ZQAA&day=1"),
        # The doorway, which writes money without separators.
        ("1618.03", '{"balance": "1618.03"}'),
        ("1618.03", '{"amount": "-1618.03"}'),
        ("9135", '{"total": "9135"}'),
        (blake.checking_mask, f'{{"mask": "{blake.checking_mask}"}}'),
        ("ZQAA", '{"symbol": "ZQAA"}'),
        ('"units": "10"', '{"units": "10", "price": "1234.56"}'),
        # The run log.
        (blake.checking_mask, f"mask={blake.checking_mask} "),
        (blake.checking_mask, f"item item-demo-0a1b2c3d4e5f: {blake.checking_mask}\n"),
        (blake.checking_mask, f"('{blake.checking_mask}',)"),
        ("access-demo-0123abcd", "token=access-demo-0123abcd\n"),
    ],
)
def test_a_secret_is_found_as_it_is_written(text: str, seen: str) -> None:
    assert household.appears(text, seen)
    assert text in household.Seen(seen)


@pytest.mark.parametrize(
    ("text", "seen"),
    [
        # Plaid's ids as the stand-in makes them, and the review ids drawn from them.
        (blake.checking_mask, f"request_id=demo3e{blake.checking_mask}fa0b1c"),
        (blake.checking_mask, f"item item-demo-{blake.checking_mask}00aa11bb"),
        (blake.checking_mask, f"item-demo-00aa11bb{blake.checking_mask}"[:22]),
        ("9135", '{"id": "r-0c9135ab77"}'),
        # Account ids, which are URL-safe base64.
        ("ZQAA", "/accounts/q2ZQAAx7Lm0pRt"),
        ("ZQAA", "/accounts/q2_ZQAAx-7Lm0pR"),
        # Another figure.
        ("9135", '{"total": "19135"}'),
        ("9135", '{"rate": "0.9135"}'),
        ("1,618", "<p>$11,618.03</p>"),
    ],
)
def test_a_secrets_text_inside_an_id_or_another_figure_is_not_that_secret(
    text: str, seen: str
) -> None:
    assert not household.appears(text, seen)
