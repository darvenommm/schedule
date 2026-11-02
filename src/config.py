from pathlib import Path
from string import Formatter

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SolverConfig(ConfigModel):
    name: str = Field(default="chuffed", min_length=1)
    time_limit_seconds: int | None = Field(default=30, ge=1)
    free_search: bool = False
    random_seed: int | None = Field(default=None, ge=0, le=2147483647)
    processes: int | None = Field(default=None, ge=1)
    optimisation_level: int = Field(default=1, ge=0, le=5)
    require_optimal: bool = False


class Preferences(ConfigModel):
    preferred_gap_days: int = Field(default=5, ge=2, le=31)
    spacing_weight: int = Field(default=1, ge=0, le=1000)
    balance_weight: int = Field(default=10, ge=0, le=1000)
    duty_role_weight: int = Field(default=20, ge=0, le=1000)
    cleaning_preferred_gap_days: int = Field(default=3, ge=2, le=31)
    cleaning_spacing_weight: int = Field(default=1, ge=0, le=1000)
    cleaning_balance_weight: int = Field(default=10, ge=0, le=1000)


class CleaningConfig(ConfigModel):
    enabled: bool = True


class ScheduleConfig(ConfigModel):
    year: int = Field(ge=1, le=9999)
    month: int = Field(ge=1, le=12)
    people_file: str = Field(default="people.yaml", min_length=1)
    output_file: str = Field(min_length=1, pattern=r"(?i)\.xlsx$")
    solver: SolverConfig = Field(default_factory=SolverConfig)
    preferences: Preferences = Field(default_factory=Preferences)
    cleaning: CleaningConfig = Field(default_factory=CleaningConfig)
    previous_month_last_day: list[str] = Field(default_factory=list)

    @field_validator("output_file")
    @classmethod
    def validate_output_template(cls, value: str) -> str:
        for _, name, spec, conversion in Formatter().parse(value):
            if name is None:
                continue
            if name not in {"year", "month"}:
                raise ValueError("В output_file допустимы только подстановки year и month.")
            if conversion is not None or spec not in {"", "d", "02d", "04d"}:
                raise ValueError("Для year и month допустимы форматы d, 02d и 04d.")
        return value

    @property
    def rendered_output_file(self) -> str:
        return self.output_file.format(year=self.year, month=self.month)


def load_config(path: str | Path) -> ScheduleConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ScheduleConfig.model_validate(data)
