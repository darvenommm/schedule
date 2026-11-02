import calendar
from dataclasses import replace
from datetime import date
from datetime import timedelta
from pathlib import Path
import importlib.util
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from pydantic import ValidationError

from src.config import CleaningConfig, ScheduleConfig, SolverConfig, load_config
from src.people import Person, load_people
from src.scheduler import DutyDay, DutySchedule, DutyScheduler, SchedulingError


ROOT = Path(__file__).resolve().parents[1]


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.people = [
            Person(
                last_name=f"Участник {i}", first_name="Имя", patronymic="Отчество",
                can_be_on_duty=i not in (1, 4), completed_duties=i,
                usually_goes_to_sirius=False,
                platoon=1 if i < 3 else 2,
            )
            for i in range(6)
        ]
        self.config = ScheduleConfig(
            year=2024, month=2, output_file="result.xlsx", cleaning=CleaningConfig(enabled=False),
        )
        self.scheduler = DutyScheduler(self.people, self.config)
        self.schedule = DutySchedule(
            people=tuple(self.people),
            days=tuple(
                DutyDay(day, (i % 2) * 3, ((i % 2) * 3 + 1, (i % 2) * 3 + 2))
                for i, day in enumerate(self.scheduler.dates)
            ),
            optimal=False, penalty=0,
        )

    def test_project_configuration_and_people(self):
        config = load_config(ROOT / "config/schedule.yaml")
        people = load_people(ROOT / "config/people.yaml")
        scheduler = DutyScheduler(people, config)
        self.assertEqual(len(people), 14)
        self.assertEqual(len(scheduler.dates), calendar.monthrange(config.year, config.month)[1])

    def test_leap_year_and_valid_schedule(self):
        self.assertEqual(self.schedule.days[-1].day, date(2024, 2, 29))
        self.scheduler.validate_schedule(self.schedule)

    def test_invalid_config(self):
        for changes in ({"month": 13}, {"year": 0}, {"output_file": "test.csv"}, {"typo": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ScheduleConfig.model_validate({**self.config.model_dump(), **changes})

    def test_dynamic_output_filename(self):
        for year, month, expected in (
            (2026, 12, "../output/duties-2026-12.xlsx"),
            (2027, 1, "../output/duties-2027-01.xlsx"),
        ):
            with self.subTest(year=year, month=month):
                config = ScheduleConfig(
                    year=year, month=month,
                    output_file="../output/duties-{year:04d}-{month:02d}.xlsx",
                )
                self.assertEqual(config.rendered_output_file, expected)
        self.assertEqual(self.config.rendered_output_file, "result.xlsx")

    def test_invalid_output_templates(self):
        for template in (
            "{day}.xlsx", "{year.real}.xlsx", "{year!r}.xlsx",
            "{month:invalid}.xlsx", "{year.xlsx", "{month:{year}}.xlsx",
        ):
            with self.subTest(template=template), self.assertRaises(ValidationError):
                ScheduleConfig(year=2026, month=12, output_file=template)

    def test_solver_settings_validation(self):
        for changes in (
            {"time_limit_seconds": 0}, {"time_limit_seconds": -1},
            {"processes": 0}, {"random_seed": -1},
            {"optimisation_level": 6}, {"require_optimal": "true"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                SolverConfig.model_validate(changes)
        self.assertIsNone(SolverConfig(time_limit_seconds=None).time_limit_seconds)

    def test_solver_options_and_unlimited_search(self):
        config = self.config.model_copy(update={"solver": SolverConfig(
            time_limit_seconds=300, free_search=True, random_seed=42,
            processes=2, optimisation_level=2,
        )})
        scheduler = DutyScheduler(self.people, config)
        options = scheduler._solver_options(["-f", "-r", "-p"])
        self.assertEqual(options, {
            "time_limit": timedelta(seconds=300), "free_search": True,
            "random_seed": 42, "processes": 2, "optimisation_level": 2,
        })
        config.solver.time_limit_seconds = None
        self.assertIsNone(scheduler._solver_options(["-f", "-r", "-p"])["time_limit"])

    def test_rejects_unsupported_solver_options(self):
        for setting, value in (("processes", 2), ("random_seed", 42), ("free_search", True)):
            with self.subTest(setting=setting):
                config = self.config.model_copy(update={
                    "solver": SolverConfig.model_validate({setting: value})
                })
                with self.assertRaisesRegex(SchedulingError, setting):
                    DutyScheduler(self.people, config)._solver_options([])

    def test_require_optimal_and_forwarding_to_solver(self):
        result = MagicMock()
        result.solution = object()
        result.status = "satisfied"
        result.objective = 123
        values = {
            "duty": [day.duty + 1 for day in self.schedule.days],
            "orderly_1": [day.orderlies[0] + 1 for day in self.schedule.days],
            "orderly_2": [day.orderlies[1] + 1 for day in self.schedule.days],
        }
        result.__getitem__.side_effect = values.__getitem__
        instance = MagicMock()
        instance.solve.return_value = result
        module = SimpleNamespace(
            default_driver=object(),
            Solver=SimpleNamespace(lookup=Mock(return_value=SimpleNamespace(stdFlags=[]))),
            Model=Mock(), Instance=Mock(return_value=instance),
            Status=SimpleNamespace(UNSATISFIABLE="unsat", OPTIMAL_SOLUTION="optimal"),
            MiniZincError=RuntimeError,
        )
        with patch.dict("sys.modules", {"minizinc": module}):
            stages = []
            schedule = self.scheduler.solve(on_stage=stages.append)
            self.assertEqual(stages, ['Подготовка модели', 'Поиск решения', 'Проверка результата'])
            self.assertFalse(schedule.optimal)
            self.assertEqual(schedule.penalty, 123)
            self.assertEqual(instance.solve.call_args.kwargs["time_limit"], timedelta(seconds=30))
            self.assertTrue(any(
                call.args == ("duty_role_weight", 20)
                for call in instance.__setitem__.call_args_list
            ))
            self.config.solver.require_optimal = True
            with self.assertRaisesRegex(SchedulingError, "оптимальность не доказана"):
                self.scheduler.solve()
            result.status = "optimal"
            self.assertTrue(self.scheduler.solve().optimal)

    def test_duplicate_people(self):
        with self.assertRaisesRegex(SchedulingError, "повторяющиеся"):
            DutyScheduler(self.people + [self.people[0]], self.config)

    def test_no_available_duty_person(self):
        people = [p.model_copy(update={"can_be_on_duty": False}) for p in self.people]
        with self.assertRaisesRegex(SchedulingError, "дежурного"):
            DutyScheduler(people, self.config)

    def test_no_wednesday_duty_person(self):
        people = [
            p.model_copy(update={
                "can_be_on_duty": i in (0, 3),
                "usually_goes_to_sirius": i in (0, 3),
            })
            for i, p in enumerate(self.people)
        ]
        with self.assertRaisesRegex(SchedulingError, "дежурного"):
            DutyScheduler(people, self.config)

    def test_missing_day(self):
        with self.assertRaisesRegex(SchedulingError, "все дни"):
            self.scheduler.validate_schedule(replace(self.schedule, days=self.schedule.days[:-1]))

    def test_two_roles_for_one_person(self):
        days = list(self.schedule.days)
        days[0] = replace(days[0], orderlies=(days[0].duty, days[0].orderlies[1]))
        with self.assertRaisesRegex(SchedulingError, "три разных"):
            self.scheduler.validate_schedule(replace(self.schedule, days=tuple(days)))

    def test_unauthorized_duty_person(self):
        days = list(self.schedule.days)
        days[0] = replace(days[0], duty=1, orderlies=(0, 2))
        with self.assertRaisesRegex(SchedulingError, "без допуска"):
            self.scheduler.validate_schedule(replace(self.schedule, days=tuple(days)))

    def test_consecutive_days(self):
        days = list(self.schedule.days)
        days[1] = replace(days[0], day=days[1].day)
        with self.assertRaisesRegex(SchedulingError, "последовательные"):
            self.scheduler.validate_schedule(replace(self.schedule, days=tuple(days)))

    def test_sirius_wednesday_for_both_roles(self):
        for person_index in (0, 2):
            with self.subTest(person_index=person_index):
                people = list(self.people)
                people[person_index] = people[person_index].model_copy(
                    update={"usually_goes_to_sirius": True}
                )
                scheduler = DutyScheduler(people, self.config)
                with self.assertRaisesRegex(SchedulingError, "среду"):
                    scheduler.validate_schedule(replace(self.schedule, people=tuple(people)))

    def test_previous_month_boundary(self):
        config = self.config.model_copy(update={
            "previous_month_last_day": [self.people[0].full_name]
        })
        scheduler = DutyScheduler(self.people, config)
        with self.assertRaisesRegex(SchedulingError, "последовательные"):
            scheduler.validate_schedule(self.schedule)

    def test_unknown_previous_month_person(self):
        config = self.config.model_copy(update={"previous_month_last_day": ["Неизвестный"]})
        with self.assertRaisesRegex(SchedulingError, "Неизвестные"):
            DutyScheduler(self.people, config)

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl не установлен")
    def test_excel_marks_counts_dates_and_history(self):
        from openpyxl import load_workbook
        from src.excel import export_excel

        with tempfile.TemporaryDirectory() as folder:
            path = export_excel(self.schedule, Path(folder) / "nested" / "duties.xlsx")
            workbook = load_workbook(path, data_only=True)
            self.assertEqual(workbook.sheetnames, ["По людям", "По дням"])
            sheet = workbook["По людям"]
            for i, person in enumerate(self.people):
                row = i + 3
                marks = [sheet.cell(row, col).value for col in range(2, 31)]
                duty, orderly = marks.count("Деж"), marks.count("Дн")
                self.assertEqual(sheet.cell(row, 31).value, duty)
                self.assertEqual(sheet.cell(row, 32).value, orderly)
                self.assertEqual(sheet.cell(row, 33).value, duty + orderly)
                self.assertEqual(sheet.cell(row, 34).value, person.completed_duties)
                self.assertEqual(sheet.cell(row, 35).value, person.completed_duties + duty + orderly)
            daily = workbook["По дням"]
            self.assertEqual(daily.max_row, 30)
            self.assertEqual(daily.cell(30, 1).value.date(), date(2024, 2, 29))
            self.assertEqual(sum(sheet.cell(row, 33).value for row in range(3, 9)), 29 * 3)
            workbook.close()

    @unittest.skipUnless(shutil.which("minizinc"), "MiniZinc не установлен")
    def test_real_solver_project_people(self):
        config = load_config(ROOT / "config/schedule.yaml")
        config = config.model_copy(update={
            "solver": config.solver.model_copy(update={
                "time_limit_seconds": 10, "require_optimal": False,
            })
        })
        scheduler = DutyScheduler(load_people(ROOT / "config/people.yaml"), config)
        schedule = scheduler.solve()
        scheduler.validate_schedule(schedule)
        self.assertEqual(sum(len(day.assigned) for day in schedule.days), len(schedule.days) * 3)

    @unittest.skipUnless(shutil.which("minizinc"), "MiniZinc не установлен")
    def test_real_solver_prefers_duty_role(self):
        people = [
            Person(
                last_name=f"Участник {i}", first_name="Имя", patronymic="Отчество",
                can_be_on_duty=i < 3, completed_duties=0, usually_goes_to_sirius=False,
                platoon=1 if i < 5 else 2,
            )
            for i in range(9)
        ]
        config = ScheduleConfig.model_validate({
            "year": 2024, "month": 2, "output_file": "unused.xlsx",
            "solver": {"name": "chuffed", "time_limit_seconds": 5, "free_search": True},
            "preferences": {"balance_weight": 0, "spacing_weight": 0, "duty_role_weight": 1},
            "cleaning": {"enabled": False},
        })
        scheduler = DutyScheduler(people, config)
        schedule = scheduler.solve()
        self.assertTrue(schedule.optimal)
        self.assertEqual(schedule.penalty, 0)
        self.assertTrue(all(
            not people[i].can_be_on_duty for day in schedule.days for i in day.orderlies
        ))


if __name__ == "__main__":
    unittest.main()
