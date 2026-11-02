from dataclasses import dataclass
from datetime import date

from .people import Person


PLATOONS = (1, 2)


@dataclass(frozen=True)
class DutyDay:
    day: date
    duty: int
    orderlies: tuple[int, int]

    @property
    def assigned(self) -> tuple[int, int, int]:
        return (self.duty, *self.orderlies)


@dataclass(frozen=True)
class CleaningDay:
    day: date
    barracks_1: tuple[int, int]
    barracks_2: tuple[int, int]
    bathrooms: tuple[int, ...]

    @property
    def assigned(self) -> tuple[int, ...]:
        return (*self.barracks_1, *self.barracks_2, *self.bathrooms)


@dataclass(frozen=True)
class PersonTotals:
    duties: int
    orderlies: int
    barracks_1: int
    barracks_2: int
    bathrooms: int

    @property
    def duty_count(self) -> int:
        return self.duties + self.orderlies

    @property
    def cleaning_count(self) -> int:
        return self.barracks_1 + self.barracks_2 + self.bathrooms

    @property
    def assignment_count(self) -> int:
        return self.duty_count + self.cleaning_count


@dataclass(frozen=True)
class DutySchedule:
    people: tuple[Person, ...]
    days: tuple[DutyDay, ...]
    optimal: bool
    penalty: int
    cleaning: tuple[CleaningDay, ...] = ()

    @property
    def status_label(self) -> str:
        return "Оптимальность доказана" if self.optimal else "Оптимальность не доказана"

    @property
    def sirius_cleaning_count(self) -> int:
        return sum(
            self.people[index].usually_goes_to_sirius
            for item in self.cleaning if item.day.weekday() == 3
            for index in item.assigned
        )

    def person_totals(self) -> tuple[PersonTotals, ...]:
        counts = [[0] * 5 for _ in self.people]
        for item in self.days:
            counts[item.duty][0] += 1
            for index in item.orderlies:
                counts[index][1] += 1
        for item in self.cleaning:
            for column, members in enumerate(
                (item.barracks_1, item.barracks_2, item.bathrooms), start=2,
            ):
                for index in members:
                    counts[index][column] += 1
        return tuple(PersonTotals(*values) for values in counts)
