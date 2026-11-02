from io import StringIO
import unittest

from src.progress import Progress


class TerminalStream(StringIO):
    def isatty(self):
        return True


class ProgressTests(unittest.TestCase):
    def test_redirected_output_contains_stages_without_control_characters(self):
        stream = StringIO()
        with Progress(total=2, stream=stream) as progress:
            progress.start_stage('Загрузка')
            progress.start_stage('Экспорт')
            progress.update_detail(1, 2, 'По людям')
        self.assertEqual(stream.getvalue(), '[1/2] Загрузка\n[2/2] Экспорт\n')
        self.assertEqual(progress.completed, 2)

    def test_disabled_progress_is_silent(self):
        stream = StringIO()
        with Progress(total=1, stream=stream, enabled=False) as progress:
            progress.start_stage('Загрузка')
        self.assertEqual(stream.getvalue(), '')

    def test_terminal_completion_and_export_detail(self):
        stream = TerminalStream()
        with Progress(total=1, stream=stream) as progress:
            progress.start_stage('Экспорт')
            progress.update_detail(6, 6, 'Сохранение файла')
        output = stream.getvalue()
        self.assertIn('[====================] 1/1', output)
        self.assertIn('6/6: Сохранение файла', output)
        self.assertFalse(progress._thread.is_alive())

    def test_failure_does_not_show_full_completion_and_stops_thread(self):
        stream = TerminalStream()
        with self.assertRaisesRegex(ValueError, 'Ошибка'):
            with Progress(total=2, stream=stream) as progress:
                progress.start_stage('Загрузка')
                progress.start_stage('Поиск')
                raise ValueError('Ошибка')
        self.assertEqual(progress.completed, 1)
        self.assertIn('1/2 × Поиск', stream.getvalue())
        self.assertNotIn('[====================]', stream.getvalue())
        self.assertFalse(progress._thread.is_alive())


if __name__ == '__main__':
    unittest.main()
