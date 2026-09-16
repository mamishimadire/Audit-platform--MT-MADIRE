from app.schemas.common import OrmModel


class RuleParametersOut(OrmModel):
    """Every parameter a shipped rule template can reference, by name, with
    the number this organization currently has for it — its own override if
    it's set one, otherwise the shipped default (see
    rule_parameter_service.DEFAULT_PARAMETERS)."""

    parameters: dict[str, float]


class RuleParametersUpdate(OrmModel):
    """Only the keys being changed — anything omitted keeps its current
    value, it is not reset to the default."""

    parameters: dict[str, float]
