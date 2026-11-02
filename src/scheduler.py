import calendar
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from .config import ScheduleConfig
from .models import PLATOONS, CleaningDay, DutyDay, DutySchedule
from .people import Person
from .progress import StageCallback

if TYPE_CHECKING:
    from minizinc import Result


class SchedulingError(ValueError):
    """Некорректные исходные данные или невозможность построить расписание."""


class DutyScheduler:
    def __init__(self, people: list[Person], config: ScheduleConfig):
        self.people = people
        self.config = config
        self.dates = tuple(
            date(config.year, config.month, day)
            for day in range(1, calendar.monthrange(config.year, config.month)[1] + 1)
        )
        self._validate_inputs()

    def _validate_inputs(self) -> None:
        names = [person.full_name for person in self.people]
        if len(names) != len(set(names)):
            raise SchedulingError("В списке пользователей есть повторяющиеся ФИО.")
        unknown = set(self.config.previous_month_last_day) - set(names)
        if unknown:
            raise SchedulingError(
                "Неизвестные ФИО в previous_month_last_day: " + ", ".join(sorted(unknown))
            )
        for day in self.dates:
            available = [
                person for person in self.people
                if not (day.weekday() == 2 and person.usually_goes_to_sirius)
                and not (
                    day.day == 1
                    and person.full_name in self.config.previous_month_last_day
                )
            ]
            if len(available) < 3:
                raise SchedulingError(f"На {day:%d.%m.%Y} доступно меньше трёх человек.")
            if not any(person.can_be_on_duty for person in available):
                raise SchedulingError(f"На {day:%d.%m.%Y} нет доступного дежурного.")
        if self.config.cleaning.enabled:
            self._validate_cleaning_inputs()

    def _validate_cleaning_inputs(self) -> None:
        previous_names = set(self.config.previous_month_last_day)
        for day in self.dates:
            available = [
                person for person in self.people
                if not (day.day == 1 and person.full_name in previous_names)
            ]
            for platoon in PLATOONS:
                if sum(p.platoon == platoon for p in available) < 2:
                    raise SchedulingError(
                        f"На {day:%d.%m.%Y} во взводе {platoon} меньше двух доступных уборщиков кубаря."
                    )
            upper_bound = len(available)
            # Трое в наряде сегодня и трое вчера — разные люди.
            upper_bound -= 6 if day.day > 1 else 3
            if upper_bound < 6:
                raise SchedulingError(
                    f"На {day:%d.%m.%Y} для уборок доступны максимум {max(0, upper_bound)} "
                    "человек, а нужны 6: по двое на каждый кубарь и на санузлы. "
                    "Люди из сегодняшнего и вчерашнего нарядов не могут убирать. "
                    "Обязательные правила несовместимы; увеличение времени не поможет."
                )

    def _sirius_priority_multiplier(self) -> int:
        """Один штраф Сириуса перевешивает все обычные штрафы вместе."""
        n, days = len(self.people), len(self.dates)
        prefs = self.config.preferences
        pair_balance = sum(
            abs(a.completed_duties - b.completed_duties) + days
            for i, a in enumerate(self.people) for b in self.people[i + 1:]
        )

        def spacing_bound(gap: int) -> int:
            return n * sum(
                (days - distance) * (gap - distance)**2
                for distance in range(1, min(gap, days + 1))
            )

        upper_bound = (
            prefs.balance_weight * pair_balance
            + prefs.spacing_weight * spacing_bound(prefs.preferred_gap_days)
            + prefs.duty_role_weight * 2 * days
        )
        if self.config.cleaning.enabled:
            within_platoons = sum(
                a.platoon == b.platoon
                for i, a in enumerate(self.people) for b in self.people[i + 1:]
            )
            upper_bound += (
                prefs.cleaning_balance_weight * days * (within_platoons + n * (n - 1) // 2)
                + prefs.cleaning_spacing_weight * spacing_bound(prefs.cleaning_preferred_gap_days)
            )
        return upper_bound + 1

    def _solver_options(self, supported_flags: list[str]) -> dict:
        settings = self.config.solver
        requested = (
            (settings.free_search, "-f", "free_search"),
            (settings.random_seed is not None, "-r", "random_seed"),
            (settings.processes is not None, "-p", "processes"),
        )
        for enabled, flag, name in requested:
            if enabled and flag not in supported_flags:
                raise SchedulingError(
                    f"Решатель {settings.name!r} не поддерживает параметр {name}. "
                    "Отключите его в YAML или выберите другой решатель."
                )
        return {
            "time_limit": (
                timedelta(seconds=settings.time_limit_seconds)
                if settings.time_limit_seconds is not None else None
            ),
            "free_search": settings.free_search,
            "random_seed": settings.random_seed,
            "processes": settings.processes,
            "optimisation_level": settings.optimisation_level,
        }

    def solve(self, on_stage: StageCallback | None = None) -> DutySchedule:
        report = on_stage if on_stage is not None else lambda label: None
        report("Подготовка модели")
        # Импорт отложен: конфигурацию и экспорт можно использовать без MiniZinc.
        import minizinc

        if minizinc.default_driver is None:
            raise SchedulingError(
                "MiniZinc не найден. Установите MiniZinc с решателем "
                "и добавьте каталог с minizinc в PATH."
            )
        try:
            solver = minizinc.Solver.lookup(self.config.solver.name)
        except LookupError as error:
            raise SchedulingError(
                f"Решатель {self.config.solver.name!r} не найден. "
                "Проверьте доступные решатели командой minizinc --solvers."
            ) from error

        options = self._solver_options(solver.stdFlags)
        model = minizinc.Model(Path(__file__).with_name("duties.mzn"))
        instance = minizinc.Instance(solver, model)
        for key, value in self._model_parameters().items():
            instance[key] = value

        report("Поиск решения")
        try:
            result = instance.solve(**options)
        except minizinc.MiniZincError as error:
            raise SchedulingError(f"Ошибка MiniZinc: {error}") from error
        report("Проверка результата")
        self._check_result(result)
        schedule = self._decode_result(result)
        self.validate_schedule(schedule)
        return schedule

    def _model_parameters(self) -> dict:
        return {
            "n_people": len(self.people),
            "n_days": len(self.dates),
            "can_be_on_duty": [p.can_be_on_duty for p in self.people],
            "usually_goes_to_sirius": [p.usually_goes_to_sirius for p in self.people],
            "completed_duties": [p.completed_duties for p in self.people],
            "worked_previous_day": [
                p.full_name in self.config.previous_month_last_day for p in self.people
            ],
            "is_wednesday": [day.weekday() == 2 for day in self.dates],
            "is_thursday": [day.weekday() == 3 for day in self.dates],
            "platoon": [person.platoon for person in self.people],
            "cleaning_enabled": self.config.cleaning.enabled,
            "sirius_priority_multiplier": self._sirius_priority_multiplier(),
            **self.config.preferences.model_dump(),
        }

    def _check_result(self, result: "Result") -> None:
        from minizinc import Status

        if result.status == Status.UNSATISFIABLE:
            raise SchedulingError(
                "Расписание невозможно: обязательные ограничения несовместимы."
            )
        if result.solution is None:
            raise SchedulingError(
                "Решение не найдено. Увеличьте time_limit_seconds или смените решатель. "
                "Отсутствие решения за отведённое время не доказывает невозможность."
            )

        if self.config.solver.require_optimal and result.status != Status.OPTIMAL_SOLUTION:
            raise SchedulingError(
                f"Найдено допустимое решение со штрафом {result.objective}, "
                "но оптимальность не доказана. require_optimal=true: Excel не выгружен. "
                "Увеличьте time_limit_seconds или задайте null для поиска без лимита."
            )

    def _decode_result(self, result: "Result") -> DutySchedule:
        from minizinc import Status

        def person(field: str, day_index: int) -> int:
            return int(result[field][day_index]) - 1

        def pair(first: str, second: str, day_index: int) -> tuple[int, int]:
            return person(first, day_index), person(second, day_index)

        return DutySchedule(
            people=tuple(self.people),
            days=tuple(
                DutyDay(
                    day=day,
                    duty=person("duty", index),
                    orderlies=pair("orderly_1", "orderly_2", index),
                )
                for index, day in enumerate(self.dates)
            ),
            optimal=result.status == Status.OPTIMAL_SOLUTION,
            penalty=int(result.objective),
            cleaning=tuple(
                CleaningDay(
                    day=day,
                    barracks_1=pair("barracks_1_a", "barracks_1_b", index),
                    barracks_2=pair("barracks_2_a", "barracks_2_b", index),
                    bathrooms=pair("bathroom_a", "bathroom_b", index),
                )
                for index, day in enumerate(self.dates)
            ) if self.config.cleaning.enabled else (),
        )

    def validate_schedule(self, schedule: DutySchedule) -> None:
        """Независимо проверить обязательные правила перед экспортом."""
        if schedule.people != tuple(self.people):
            raise SchedulingError("Результат относится к другому списку пользователей.")
        if tuple(item.day for item in schedule.days) != self.dates:
            raise SchedulingError("Результат должен содержать все дни выбранного месяца.")
        previous = self._previous_duty_indices()
        for item in schedule.days:
            assigned = set(item.assigned)
            if len(assigned) != 3 or any(i < 0 or i >= len(self.people) for i in assigned):
                raise SchedulingError("На каждый день нужны три разных человека.")
            if not self.people[item.duty].can_be_on_duty:
                raise SchedulingError("Дежурным назначен человек без допуска.")
            if assigned & previous:
                raise SchedulingError("Обнаружены наряды в последовательные дни.")
            if item.day.weekday() == 2 and any(
                self.people[i].usually_goes_to_sirius for i in assigned
            ):
                raise SchedulingError("В среду назначен человек, ездящий в Сириус.")
            previous = assigned

        self._validate_cleaning_schedule(schedule)

    def _validate_cleaning_schedule(self, schedule: DutySchedule) -> None:
        if not self.config.cleaning.enabled:
            if schedule.cleaning:
                raise SchedulingError("Уборки отключены, но присутствуют в результате.")
            return
        if tuple(item.day for item in schedule.cleaning) != self.dates:
            raise SchedulingError("Графики уборок должны содержать все дни месяца.")
        previous = self._previous_duty_indices()
        for duty, cleaning in zip(schedule.days, schedule.cleaning):
            for members, count in (
                (cleaning.barracks_1, 2), (cleaning.barracks_2, 2),
                (cleaning.bathrooms, 2),
            ):
                if len(members) != count:
                    raise SchedulingError("Неверное количество уборщиков в графике.")
            assigned = set(cleaning.assigned)
            if len(assigned) != len(cleaning.assigned) or any(
                i < 0 or i >= len(self.people) for i in assigned
            ):
                raise SchedulingError("Человек не может выполнять две уборки в один день.")
            for platoon, members in ((1, cleaning.barracks_1), (2, cleaning.barracks_2)):
                if any(self.people[i].platoon != platoon for i in members):
                    raise SchedulingError("Уборщик кубаря назначен из другого взвода.")
            if assigned & set(duty.assigned):
                raise SchedulingError("Уборка назначена в день наряда.")
            if assigned & previous:
                raise SchedulingError("Уборка назначена на следующий день после наряда.")
            previous = set(duty.assigned)

    def _previous_duty_indices(self) -> set[int]:
        return {
            index for index, person in enumerate(self.people)
            if person.full_name in self.config.previous_month_last_day
        }
