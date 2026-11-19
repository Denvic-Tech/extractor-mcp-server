"""Запросы чтения метаданных 1С и области счетов."""
from pydantic import Field, StrictBool, StrictInt, model_validator

from ..identifiers import NAME, OBJECT_NAME_PATTERN, OBJECT_PATH_PATTERN, has_control_characters
from ..requests import StrictRequest


class AccountScope(StrictRequest):
    codes: list[str] = Field(min_length=1, max_length=20)
    includeSubaccounts: StrictBool = True
    maxAccounts: StrictInt = Field(default=200, ge=1, le=1000)
    maxKinds: StrictInt = Field(default=1000, ge=1, le=5000)

    @model_validator(mode="after")
    def valid_codes(self):
        if any(not c.strip() or len(c) > 50 or has_control_characters(c) for c in self.codes):
            raise ValueError("Invalid account code")
        if len(set(self.codes)) != len(self.codes):
            raise ValueError("Duplicate account codes")
        return self


class AccountAnalyticsRequest(AccountScope):
    object: str = Field(pattern=r"^(?:РегистрБухгалтерии|ПланСчетов)\." + NAME + "$")


class ObjectRequest(StrictRequest):
    object: str = Field(pattern=OBJECT_NAME_PATTERN, max_length=512)


class DescribeObjectRequest(StrictRequest):
    # Two first components identify a metadata object. Further components are
    # reference fields whose target object becomes the root for describe.
    object: str = Field(pattern=OBJECT_PATH_PATTERN, max_length=512)
