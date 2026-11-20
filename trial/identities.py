from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPair:
    alpha: str
    beta: str

    @property
    def ordered(self) -> tuple[str, str]:
        return self.alpha, self.beta


def require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{field} must be nonblank text without NUL')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'{field} must be UTF-8 text') from error
    return value


def build_model_pair(alpha: object, beta: object) -> ModelPair:
    alpha_id = require_identifier(alpha, 'alpha model')
    beta_id = require_identifier(beta, 'beta model')
    if alpha_id == beta_id:
        raise ValueError('alpha and beta models must be distinct')
    return ModelPair(alpha_id, beta_id)


def require_model(models: ModelPair, value: object) -> str:
    model_id = require_identifier(value, 'model')
    if model_id not in models.ordered:
        raise ValueError(f'model {model_id!r} is outside the frozen pair')
    return model_id
