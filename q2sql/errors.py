"""Named exceptions for the contract-to-SQL generator.

Every failure in this package raises one of these, with a message that says what
was wrong and where. Nothing is ever allowed to fall through to silently-wrong SQL.
"""


class ContractToSqlError(Exception):
    """Base class for every error this package raises."""


class InvalidModelError(ContractToSqlError):
    """The semantic model itself is malformed (bad shape, dangling references)."""


class InvalidContractError(ContractToSqlError):
    """The contract is malformed, or internally inconsistent, independent of the model."""


class UnknownMetricError(ContractToSqlError):
    """A contract names a metric that isn't declared in the semantic model."""


class UnknownDimensionError(ContractToSqlError):
    """A contract names a dimension that isn't declared in the semantic model."""


class NoJoinPathError(ContractToSqlError):
    """No path of declared relationships connects the anchor dataset to a needed one."""


class AmbiguousJoinPathError(ContractToSqlError):
    """More than one shortest join path connects the anchor to a needed dataset."""


class UnsupportedContractError(ContractToSqlError):
    """The contract is well-formed and valid against the model, but asks for something
    this generator does not (yet) implement."""
