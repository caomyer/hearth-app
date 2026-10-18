import logging
import pickle

import pytest

from hearth.vault.secret import KeyMaterial, Secret
from tests.vault.conftest import TOKEN


def test_a_secret_never_prints(caplog: pytest.LogCaptureFixture) -> None:
    secret = Secret(TOKEN)
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("test").info("token %s %r", secret, secret)

    rendered = [repr(secret), str(secret), f"{secret}", f"{secret!r}", "%s" % secret]  # noqa: UP031 - %-formatting is what logging uses
    rendered += [format(secret, ">80"), caplog.text, repr([secret]), repr({"t": secret})]

    assert all(TOKEN not in text for text in rendered)
    assert "***" in str(secret)


def test_a_secret_reveals_its_value_only_when_asked() -> None:
    assert Secret(TOKEN).reveal() == TOKEN


def test_secrets_compare_by_value_but_cannot_be_hashed_or_pickled() -> None:
    assert Secret(TOKEN) == Secret(TOKEN)
    assert Secret(TOKEN) != Secret(TOKEN + "x")
    with pytest.raises(TypeError):
        hash(Secret(TOKEN))
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(Secret(TOKEN))


@pytest.mark.parametrize("value", ["", b"bytes", None])
def test_a_secret_must_be_a_non_empty_string(value: object) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        Secret(value)  # type: ignore[arg-type]


def test_key_material_never_prints_either() -> None:
    key = KeyMaterial(b"\x01" * 32)

    assert repr(key) == str(key) == f"{key}" == "KeyMaterial('***')"
    assert key.reveal() == b"\x01" * 32
    with pytest.raises(TypeError, match="cannot be pickled"):
        pickle.dumps(key)
    with pytest.raises(ValueError, match="non-empty bytes"):
        KeyMaterial(b"")
