from dataclasses import replace
from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from src.config import ScheduleConfig
from src.people import Person, load_people
from src.scheduler import CleaningDay, DutyDay, DutySchedule, DutyScheduler, SchedulingError


class CleaningTests(unittest.TestCase):
    def setUp(self):
        self.people = [
            Person(
                last_name=f"Человек {i}", first_name="Имя", patronymic="Отчество",
                can_be_on_duty=True, completed_duties=0, usually_goes_to_sirius=False,
                platoon=1 if i < 7 else 2,
            )
            for i in range(14)
        ]
        self.config = ScheduleConfig(year=2024, month=2, output_file="result.xlsx")
        self.scheduler = DutyScheduler(self.people, self.config)
        self.schedule = DutySchedule(
            people=tuple(self.people), optimal=False, penalty=0,
            days=tuple(
                DutyDay(day, 0, (7,8)) if i % 2 == 0 else DutyDay(day, 1, (2,9))
                for i, day in enumerate(self.scheduler.dates)
            ),
            cleaning=tuple(
                CleaningDay(day, (3,4), (10,11), (5,12)) for day in self.scheduler.dates
            ),
        )

    def mutate_first(self, **changes):
        cleaning = list(self.schedule.cleaning)
        cleaning[0] = replace(cleaning[0], **changes)
        return replace(self.schedule, cleaning=tuple(cleaning))

    def test_valid_four_schedules(self):
        self.scheduler.validate_schedule(self.schedule)
        self.assertEqual(sum(len(item.assigned) for item in self.schedule.cleaning), 29*6)

    def test_correct_project_platoons(self):
        people = load_people(Path(__file__).resolve().parents[1] / 'config/people.yaml')
        self.assertEqual(
            {p.last_name for p in people if p.platoon == 1},
            {'Латута', 'Бугаков', 'Вишневский', 'Москалёв', 'Назаров', 'Фомин', 'Захоженко'},
        )
        self.assertEqual(sum(p.platoon == 2 for p in people), 7)

    def test_platoon_validation(self):
        with self.assertRaises(ValidationError):
            Person.model_validate({**self.people[0].model_dump(), 'platoon': 3})

    def test_missing_cleaning_day(self):
        with self.assertRaisesRegex(SchedulingError, 'все дни'):
            self.scheduler.validate_schedule(replace(self.schedule, cleaning=self.schedule.cleaning[:-1]))

    def test_exactly_two_cleaners(self):
        with self.assertRaisesRegex(SchedulingError, 'количество'):
            self.scheduler.validate_schedule(self.mutate_first(bathrooms=(5,)))

    def test_cannot_clean_two_places(self):
        with self.assertRaisesRegex(SchedulingError, 'две уборки'):
            self.scheduler.validate_schedule(self.mutate_first(bathrooms=(3,12)))

    def test_barracks_own_platoon(self):
        with self.assertRaisesRegex(SchedulingError, 'другого взвода'):
            self.scheduler.validate_schedule(self.mutate_first(barracks_1=(10,4), barracks_2=(3,11)))

    def test_no_cleaning_on_duty_day(self):
        with self.assertRaisesRegex(SchedulingError, 'в день наряда'):
            self.scheduler.validate_schedule(self.mutate_first(bathrooms=(0,12)))

    def test_no_cleaning_after_duty(self):
        cleaning = list(self.schedule.cleaning)
        cleaning[1] = replace(cleaning[1], bathrooms=(0,12))
        with self.assertRaisesRegex(SchedulingError, 'следующий день'):
            self.scheduler.validate_schedule(replace(self.schedule, cleaning=tuple(cleaning)))

    def test_previous_month_blocks_first_cleaning(self):
        config = self.config.model_copy(update={'previous_month_last_day': [self.people[3].full_name]})
        with self.assertRaisesRegex(SchedulingError, 'следующий день'):
            DutyScheduler(self.people, config).validate_schedule(self.schedule)

    def test_sirius_thursday_is_allowed_but_counted(self):
        people = list(self.people)
        people[3] = people[3].model_copy(update={'usually_goes_to_sirius': True})
        scheduler = DutyScheduler(people, self.config)
        schedule = replace(self.schedule, people=tuple(people))
        scheduler.validate_schedule(schedule)
        self.assertEqual(schedule.sirius_cleaning_count, 5)

    def test_not_enough_people_for_four_schedules(self):
        with self.assertRaisesRegex(SchedulingError, 'максимум'):
            DutyScheduler(self.people[:10], self.config)

    @unittest.skipUnless(importlib.util.find_spec('openpyxl'), 'openpyxl не установлен')
    def test_excel_all_graphs_and_totals(self):
        from openpyxl import load_workbook
        from src.excel import export_excel

        people = list(self.people)
        people[3] = people[3].model_copy(update={'usually_goes_to_sirius': True})
        schedule = replace(self.schedule, people=tuple(people))
        with tempfile.TemporaryDirectory() as folder:
            updates = []
            path = export_excel(
                schedule, Path(folder) / 'all.xlsx',
                on_progress=lambda *values: updates.append(values),
            )
            self.assertEqual([update[0] for update in updates], [1, 2, 3, 4, 5, 6, 6])
            self.assertTrue(all(update[1] == 6 for update in updates))
            self.assertEqual(updates[-1][2], 'Сохранение файла')
            workbook = load_workbook(path, data_only=True)
            self.assertEqual(workbook.sheetnames, [
                'По людям', 'По дням', 'Кубарь 1 взвод', 'Кубарь 2 взвод', 'Санузлы', 'Итоги',
            ])
            for name, size in [('Кубарь 1 взвод', 7), ('Кубарь 2 взвод', 7), ('Санузлы', 14)]:
                sheet = workbook[name]
                self.assertEqual(sum(sheet.cell(row,31).value for row in range(3,size+3)), 29*2)
            summary = workbook['Итоги']
            self.assertEqual(sum(summary.cell(row,5).value for row in range(2,16)), 29*3)
            self.assertEqual(sum(summary.cell(row,9).value for row in range(2,16)), 29*6)
            self.assertEqual(sum(summary.cell(row,10).value for row in range(2,16)), 29*9)
            # Уборка перед Сириусом оформляется так же, как любая другая.
            grid = workbook['Кубарь 1 взвод']
            self.assertEqual(grid.cell(6,2).fill.fgColor.rgb, grid.cell(6,3).fill.fgColor.rgb)
            self.assertEqual(workbook['По дням'].max_column, 11)
            for sheet in workbook:
                for row in sheet:
                    for cell in row:
                        self.assertNotIn('Сириус', str(cell.value))
                        self.assertNotEqual(cell.fill.fgColor.rgb, '00FFE699')
            workbook.close()

    @unittest.skipUnless(importlib.util.find_spec('openpyxl'), 'openpyxl не установлен')
    def test_failed_save_preserves_previous_excel(self):
        from src.excel import export_excel

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'existing.xlsx'
            destination.write_bytes(b'previous workbook')
            with patch('src.excel.Workbook.save', side_effect=OSError('Disk full')):
                with self.assertRaisesRegex(OSError, 'Disk full'):
                    export_excel(self.schedule, destination)
            self.assertEqual(destination.read_bytes(), b'previous workbook')
            self.assertEqual(list(Path(folder).iterdir()), [destination])


if __name__ == '__main__':
    unittest.main()
