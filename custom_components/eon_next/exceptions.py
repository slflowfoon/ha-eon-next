"""Sanitized exceptions shared by the API and authentication clients."""


class EonNextError(Exception):
    """Base E.ON Next exception."""


class EonNextAuthenticationError(EonNextError):
    """Authentication must be renewed interactively."""


class EonNextConnectionError(EonNextError):
    """E.ON Next is temporarily unreachable."""


class EonNextResponseError(EonNextError):
    """A response or persisted session was invalid."""
