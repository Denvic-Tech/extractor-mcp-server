"""Ранняя проверка возможностей EPA до того, как сценарий что-либо изменит."""
from ..domain.epa.capabilities import CAPABILITY_REQUIRED, PublishedEpa
from .errors import ApplicationError
from .ports import CallApi


def published_epa(call_api: CallApi) -> PublishedEpa:
    """Одно чтение service/readiness выбранной базы."""
    return PublishedEpa.from_readiness(call_api("service/readiness", {}))


def require_capabilities(call_api: CallApi, *names: str) -> PublishedEpa:
    """Остановить сценарий, если EPA не объявила хотя бы одну из возможностей."""
    epa = published_epa(call_api)
    missing = epa.first_missing(*names)
    if missing is not None:
        raise ApplicationError(CAPABILITY_REQUIRED[missing])
    return epa
