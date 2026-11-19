"""Отказы журнала планов и операций. Текст каждого безопасен для клиента MCP."""


class OperationStoreError(RuntimeError):
    """Base error for safe plan/operation handling."""


class PlanNotFound(OperationStoreError):
    pass


class PlanExpired(OperationStoreError):
    pass


class PlanConflict(OperationStoreError):
    pass


class ScenarioConflict(OperationStoreError):
    """A concurrent scenario choice won; reread the same durable scenario."""


class OperationIndeterminate(OperationStoreError):
    pass


class OperationRejected(OperationStoreError):
    def __init__(self, result):
        self.result = result
        super().__init__(
            f"{result['code']}: {result['error']} Запись не выполнялась. Для новой попытки требуется новая операция.")
