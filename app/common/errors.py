class DomainError(Exception):
    """Raised when a domain invariant or lifecycle rule is violated."""


class InvalidTransitionError(DomainError):
    pass


class PreconditionError(DomainError):
    pass


class AuthorizationError(DomainError):
    pass
