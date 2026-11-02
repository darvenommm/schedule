from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Person(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    last_name: str = Field(min_length=1)
    first_name: str = Field(min_length=1)
    patronymic: str = Field(min_length=1)
    can_be_on_duty: bool
    completed_duties: int = Field(ge=0)
    usually_goes_to_sirius: bool
    platoon: int = Field(ge=1, le=2)

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_name} {self.patronymic}"


class PeopleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    people: list[Person]


def load_people(path: str | Path) -> list[Person]:
    """Загрузить пользователей из YAML и проверить структуру данных."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return PeopleConfig.model_validate(data).people
