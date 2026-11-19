"""Расписание проекта: ежедневное, еженедельное или интервальное в часовом поясе 1С."""
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .projects.refs import ProjectScheduleRef
from .requests import StrictRequest


class ScheduleDefinition(StrictRequest):
    """The first-stage scheduling subset; times belong to the 1C time context."""
    kind: Literal["daily", "weekly", "interval"]
    time: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")
    weekdays: list[StrictInt] | None = Field(default=None, min_length=1, max_length=7)
    intervalMinutes: StrictInt | None = Field(default=None, ge=1, le=1440)
    startTime: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")
    endTime: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")

    @model_validator(mode="after")
    def valid_schedule(self):
        if self.kind == "interval":
            if self.time is not None or self.weekdays is not None:
                raise ValueError("Interval schedules do not accept time or weekdays")
            if self.intervalMinutes is None or self.startTime is None or self.endTime is None:
                raise ValueError("Interval schedules require intervalMinutes, startTime and endTime")
            if self.startTime >= self.endTime:
                raise ValueError("Interval window must end after it starts, within the same day")
        else:
            if self.time is None:
                raise ValueError("Daily and weekly schedules require time")
            if any(value is not None for value in (self.intervalMinutes, self.startTime, self.endTime)):
                raise ValueError("Interval fields require kind=interval")
            if self.kind == "daily" and self.weekdays is not None:
                raise ValueError("Daily schedules do not accept weekdays")
            if self.kind == "weekly":
                if not self.weekdays or any(day < 1 or day > 7 for day in self.weekdays):
                    raise ValueError("Weekly schedules require weekdays 1 (Monday) to 7 (Sunday)")
                if len(set(self.weekdays)) != len(self.weekdays):
                    raise ValueError("Duplicate weekdays")
        return self


class ProjectSchedulePreview(ProjectScheduleRef):
    enabled: StrictBool
    schedule: ScheduleDefinition | None = None


class ProjectScheduleSet(ProjectSchedulePreview):
    expectedScheduleState: str = Field(min_length=1, max_length=524288)
