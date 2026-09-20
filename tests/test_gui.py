import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QImage, QPalette
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from slides_docx.gui.app import MainWindow
    from slides_docx.gui.theme import DARK_THEME, LIGHT_THEME, system_theme, stylesheet
    from slides_docx.gui.widgets import CropView
    from slides_docx.profiles import ProfileStore

    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is required for GUI tests")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication(["slides-docx-tests"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_crop_view_maps_drag_to_source_coordinates(self):
        image_path = self.root / "preview.png"
        image = QImage(640, 360, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        self.assertTrue(image.save(str(image_path)))
        view = CropView()
        view.resize(640, 360)
        view.set_image(image_path)
        view.show()
        self.application.processEvents()
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(64, 36))
        QTest.mouseMove(view, QPoint(576, 324), delay=10)
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(576, 324))
        width, height, x, y = view.selected_crop()
        self.assertAlmostEqual(x, 64, delta=2)
        self.assertAlmostEqual(y, 36, delta=2)
        self.assertAlmostEqual(width, 512, delta=2)
        self.assertAlmostEqual(height, 288, delta=2)
        view.close()

    def test_main_window_has_guided_pages_and_discovers_matching_vtt(self):
        video = self.root / "lécture.mp4"
        vtt = self.root / "lécture.vtt"
        video.touch()
        vtt.write_text("WEBVTT\n", encoding="utf-8")
        window = MainWindow(ProfileStore(self.root / "config.json"))
        window.video_path.setText(video)
        self.application.processEvents()
        self.assertEqual(window.pages.count(), 4)
        self.assertEqual(Path(window.vtt_path.text()), vtt)
        self.assertEqual(Path(window.output_folder.text()), self.root)
        window.close()

    def test_full_frame_allows_crop_step_without_profile(self):
        window = MainWindow(ProfileStore(self.root / "config.json"))
        window.pages.setCurrentIndex(1)
        window.full_frame.setChecked(True)
        window._next()
        self.assertEqual(window.pages.currentIndex(), 2)
        window.close()

    def test_disabling_slide_images_disables_screenshot_lead(self):
        window = MainWindow(ProfileStore(self.root / "config.json"))
        self.assertTrue(window.slide_images.isChecked())
        self.assertTrue(window.lead.isEnabled())
        window.slide_images.setChecked(False)
        self.assertFalse(window.lead.isEnabled())
        window.close()

    def test_theme_follows_explicit_system_color_scheme(self):
        self.assertIs(
            system_theme(self.application, Qt.ColorScheme.Dark), DARK_THEME
        )
        self.assertIs(
            system_theme(self.application, Qt.ColorScheme.Light), LIGHT_THEME
        )
        self.assertIn(DARK_THEME.window, stylesheet(DARK_THEME))
        self.assertIn(LIGHT_THEME.window, stylesheet(LIGHT_THEME))

    def test_unknown_color_scheme_uses_palette_brightness(self):
        original = self.application.palette()
        try:
            palette = QPalette(original)
            palette.setColor(QPalette.ColorRole.Window, QColor("#101216"))
            self.application.setPalette(palette)
            self.assertIs(
                system_theme(self.application, Qt.ColorScheme.Unknown), DARK_THEME
            )
        finally:
            self.application.setPalette(original)


if __name__ == "__main__":
    unittest.main()
