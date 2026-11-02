from collections.abc import Sequence
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .models import PLATOONS, DutySchedule, PersonTotals
from .progress import DetailCallback


WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
DUTY_FILL = PatternFill("solid", fgColor="BDD7EE")
ASSIGNMENT_FILL = PatternFill("solid", fgColor="C6E0B4")
WEEKEND_FILL = PatternFill("solid", fgColor="F2F2F2")
HEADER_FILL = PatternFill("solid", fgColor="305496")


def _day_headers(schedule: DutySchedule) -> list[str]:
    return [f"{item.day.day}\n{WEEKDAYS[item.day.weekday()]}" for item in schedule.days]


def _format_sheet(sheet: Worksheet, header_row: int) -> None:
    for cell in sheet[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.auto_filter.ref = (
        f"A{header_row}:{get_column_letter(sheet.max_column)}{sheet.max_row}"
    )
    sheet.print_title_rows = f"1:{header_row}"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0


def _format_grid(sheet: Worksheet, day_count: int) -> None:
    _format_sheet(sheet, header_row=2)
    sheet.freeze_panes = "B3"
    sheet.row_dimensions[2].height = 34
    sheet.column_dimensions["A"].width = 44
    for column in range(2, sheet.max_column + 1):
        sheet.column_dimensions[get_column_letter(column)].width = (
            5 if column <= day_count + 1 else 19
        )
        for row in range(3, sheet.max_row + 1):
            sheet.cell(row, column).alignment = Alignment(horizontal="center")


def _append_status(sheet: Worksheet, schedule: DutySchedule) -> None:
    sheet.append([])
    sheet.append([schedule.status_label, f"Штраф: {schedule.penalty}"])


def _duty_sheet(
    workbook: Workbook, schedule: DutySchedule, totals: tuple[PersonTotals, ...],
) -> Worksheet:
    sheet = workbook.create_sheet("По людям")
    sheet.append([f"Наряды за {schedule.days[0].day:%m.%Y}"])
    sheet.append([
        "ФИО", *_day_headers(schedule), "Дежурств", "Дневальных",
        "Всего за месяц", "До месяца", "Всего с историей",
    ])
    for index, (person, counts) in enumerate(zip(schedule.people, totals)):
        marks = [
            "Деж" if item.duty == index else "Дн" if index in item.orderlies else ""
            for item in schedule.days
        ]
        sheet.append([
            person.full_name, *marks, counts.duties, counts.orderlies, counts.duty_count,
            person.completed_duties, person.completed_duties + counts.duty_count,
        ])
        for column, (item, mark) in enumerate(zip(schedule.days, marks), start=2):
            cell = sheet.cell(sheet.max_row, column)
            if mark:
                cell.fill = DUTY_FILL if mark == "Деж" else ASSIGNMENT_FILL
            elif item.day.weekday() >= 5:
                cell.fill = WEEKEND_FILL
    _format_grid(sheet, len(schedule.days))
    sheet.append([])
    sheet.append(["Деж — дежурный; Дн — дневальный. Оба назначения считаются нарядом."])
    _append_status(sheet, schedule)
    return sheet


def _daily_sheet(workbook: Workbook, schedule: DutySchedule) -> Worksheet:
    sheet = workbook.create_sheet("По дням")
    headers = ["Дата", "День недели", "Дежурный", "Дневальный 1", "Дневальный 2"]
    if schedule.cleaning:
        headers.extend([
            "Кубарь 1 — уборщик 1", "Кубарь 1 — уборщик 2",
            "Кубарь 2 — уборщик 1", "Кубарь 2 — уборщик 2",
            "Санузлы — уборщик 1", "Санузлы — уборщик 2",
        ])
    sheet.append(headers)
    for index, item in enumerate(schedule.days):
        assigned = item.assigned
        if schedule.cleaning:
            assigned += schedule.cleaning[index].assigned
        sheet.append([
            item.day, WEEKDAYS[item.day.weekday()],
            *[schedule.people[person].full_name for person in assigned],
        ])
        sheet.cell(sheet.max_row, 1).number_format = "DD.MM.YYYY"
    sheet.freeze_panes = "C2"
    for column in range(1, sheet.max_column + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 14 if column < 3 else 44
    _format_sheet(sheet, header_row=1)
    return sheet


def _cleaning_sheet(
    workbook: Workbook,
    schedule: DutySchedule,
    title: str,
    members: Sequence[int],
    assignments: Sequence[tuple[int, ...]],
) -> Worksheet:
    sheet = workbook.create_sheet(title)
    sheet.append([f"{title} за {schedule.days[0].day:%m.%Y}"])
    sheet.append(["ФИО", *_day_headers(schedule), "Всего уборок"])
    for index in members:
        marks = ["У" if index in assigned else "" for assigned in assignments]
        sheet.append([schedule.people[index].full_name, *marks, marks.count("У")])
        for column, mark in enumerate(marks, start=2):
            if mark:
                sheet.cell(sheet.max_row, column).fill = ASSIGNMENT_FILL
    _format_grid(sheet, len(schedule.days))
    sheet.append([])
    sheet.append(["У — уборка."])
    return sheet


def _summary_sheet(
    workbook: Workbook, schedule: DutySchedule, totals: tuple[PersonTotals, ...],
) -> Worksheet:
    sheet = workbook.create_sheet("Итоги")
    sheet.append([
        "ФИО", "Взвод", "Дежурств", "Дневальных", "Нарядов за месяц",
        "Кубарь 1", "Кубарь 2", "Санузлы", "Всего уборок", "Всего назначений за месяц",
    ])
    for person, counts in zip(schedule.people, totals):
        sheet.append([
            person.full_name, person.platoon, counts.duties, counts.orderlies,
            counts.duty_count, counts.barracks_1, counts.barracks_2, counts.bathrooms,
            counts.cleaning_count, counts.assignment_count,
        ])
    sheet.freeze_panes = "C2"
    sheet.column_dimensions["A"].width = 44
    for column in range(2, sheet.max_column + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 24
    _format_sheet(sheet, header_row=1)
    _append_status(sheet, schedule)
    return sheet


def export_excel(
    schedule: DutySchedule, path: str | Path, on_progress: DetailCallback | None = None,
) -> Path:
    workbook = Workbook()
    workbook.remove(workbook.active)
    totals = schedule.person_totals()
    writers = [
        partial(_duty_sheet, workbook, schedule, totals),
        partial(_daily_sheet, workbook, schedule),
    ]
    if schedule.cleaning:
        for platoon in PLATOONS:
            writers.append(partial(
                _cleaning_sheet, workbook, schedule, f"Кубарь {platoon} взвод",
                [i for i, person in enumerate(schedule.people) if person.platoon == platoon],
                [item.barracks_1 if platoon == 1 else item.barracks_2 for item in schedule.cleaning],
            ))
        writers.extend([
            partial(
                _cleaning_sheet, workbook, schedule, "Санузлы", range(len(schedule.people)),
                [item.bathrooms for item in schedule.cleaning],
            ),
            partial(_summary_sheet, workbook, schedule, totals),
        ])
    try:
        for completed, writer in enumerate(writers, start=1):
            sheet = writer()
            if on_progress is not None:
                on_progress(completed, len(writers), sheet.title)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if on_progress is not None:
            on_progress(len(writers), len(writers), "Сохранение файла")
        _save_workbook(workbook, destination)
        return destination
    finally:
        workbook.close()


def _save_workbook(workbook: Workbook, destination: Path) -> None:
    """Заменить предыдущий файл только после успешного сохранения нового."""
    with NamedTemporaryFile(dir=destination.parent, suffix=".xlsx", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        workbook.save(temporary_path)
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
