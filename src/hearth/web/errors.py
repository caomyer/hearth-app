"""Outcomes a request can end in other than the page it asked for."""


class SignInRequired(Exception):  # noqa: N818 - it names an outcome, not a fault
    """No live session. Answered with a redirect to sign in."""


class PasswordChangeRequired(Exception):  # noqa: N818
    """The member signed in with a one-time password and must choose their own first."""


class FormExpired(Exception):  # noqa: N818
    """A form came back without the token of the session it was issued to."""
