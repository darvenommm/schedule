import argparse
import sys
from pathlib import Path

import yaml

from .config import load_config
from .people import load_people
from .progress import Progress
from .scheduler import DutyScheduler


def main() -> int:
    parser = argparse.ArgumentParser(description="Расчёт нарядов и трёх графиков уборок на месяц")
    parser.add_argument("--config", type=Path, default=Path("config/schedule.yaml"))
    parser.add_argument("--no-progress", action="store_true", help="Не показывать этапы и индикаторы")
    args = parser.parse_args()
    try:
        with Progress(total=7, enabled=not args.no_progress) as progress:
            progress.start_stage("Загрузка конфигурации")
            config_path = args.config.resolve()
            config = load_config(config_path)
            progress.start_stage("Загрузка пользователей")
            people = load_people(config_path.parent / config.people_file)
            progress.start_stage("Проверка исходных данных")
            scheduler = DutyScheduler(people, config)
            schedule = scheduler.solve(on_stage=progress.start_stage)
            progress.start_stage("Экспорт Excel")
            from .excel import export_excel

            output = export_excel(
                schedule, config_path.parent / config.rendered_output_file,
                on_progress=progress.update_detail,
            )
    except KeyboardInterrupt:
        print("Расчёт прерван.", file=sys.stderr)
        return 130
    except ImportError as error:
        print(f"Не установлена зависимость {error.name!r}. Выполните uv sync.", file=sys.stderr)
        return 1
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    print(f"Расписания на {config.month:02d}.{config.year}, решатель {config.solver.name}.")
    print(f"Excel: {output.resolve()}")
    print(f"Штраф: {schedule.penalty} (меньше — лучше при одинаковых данных и весах).")
    print(
        "Оптимальность доказана." if schedule.optimal
        else "Найдено допустимое расписание; оптимальность не доказана."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
