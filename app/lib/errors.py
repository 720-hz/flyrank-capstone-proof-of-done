class AuthError(Exception):
    """401 — missing or invalid API key."""


class ForbiddenError(Exception):
    """403 — valid key, but not allowed to touch this resource."""


class NotFoundError(Exception):
    """404."""


class ValidationError(Exception):
    """422 — well-formed request, bad content (e.g. unknown evidence_type)."""
