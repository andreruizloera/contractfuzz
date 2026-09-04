"""Exception types for contractfuzz.

All expected failure modes raise a subclass of ContractfuzzError so the CLI
can print a clean message and exit nonzero instead of dumping a traceback.
"""


class ContractfuzzError(Exception):
    """Base class for expected, user-facing errors."""


class SpecError(ContractfuzzError):
    """The OpenAPI document is missing, malformed, or uses an unsupported feature."""


class UnsupportedSchemaError(ContractfuzzError):
    """A schema is valid but contractfuzz cannot generate data for it yet."""
