from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class Collapsible(QWidget):
    def __init__(self, title, content, parent=None):
        super().__init__(parent)
        self.toggle = QToolButton(text=title, checkable=True, checked=False)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.content = content
        self.content.setVisible(False)
        self.toggle.toggled.connect(self._toggle)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)

    def _toggle(self, expanded):
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.content.setVisible(expanded)


class CropView(QLabel):
    cropChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setText("Load a preview frame to select the slide area")
        self.setStyleSheet("background: #16191f; color: #b7bec9; border-radius: 8px;")
        self._source = QPixmap()
        self._display_rect = QRect()
        self._selection = QRect()
        self._origin = QPoint()
        self._dragging = False

    def set_image(self, path, crop=None):
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise ValueError(f"Could not display preview image: {path}")
        self._source = pixmap
        if crop:
            width, height, x, y = crop
            self._selection = QRect(x, y, width, height)
        else:
            self._selection = QRect()
        self.update()

    def clear_selection(self):
        self._selection = QRect()
        self.cropChanged.emit(None)
        self.update()

    def selected_crop(self):
        rectangle = self._selection.normalized().intersected(self._source.rect())
        if rectangle.width() < 2 or rectangle.height() < 2:
            return None
        return rectangle.width(), rectangle.height(), rectangle.x(), rectangle.y()

    def _fit_rect(self):
        if self._source.isNull():
            return QRect()
        size = self._source.size()
        size.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect(
            (self.width() - size.width()) // 2,
            (self.height() - size.height()) // 2,
            size.width(),
            size.height(),
        )

    def _to_source(self, point):
        display = self._display_rect
        if display.isEmpty() or not display.contains(point):
            return None
        x = round((point.x() - display.x()) * self._source.width() / display.width())
        y = round((point.y() - display.y()) * self._source.height() / display.height())
        return QPoint(
            max(0, min(self._source.width() - 1, x)),
            max(0, min(self._source.height() - 1, y)),
        )

    def _to_display(self, rectangle):
        display = self._display_rect
        if display.isEmpty() or self._source.isNull():
            return QRect()
        return QRect(
            display.x() + round(rectangle.x() * display.width() / self._source.width()),
            display.y() + round(rectangle.y() * display.height() / self._source.height()),
            round(rectangle.width() * display.width() / self._source.width()),
            round(rectangle.height() * display.height() / self._source.height()),
        )

    def paintEvent(self, event):
        if self._source.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._display_rect = self._fit_rect()
        painter.drawPixmap(self._display_rect, self._source)
        if not self._selection.isEmpty():
            selected = self._to_display(self._selection.normalized())
            painter.save()
            painter.setClipRegion(QRegion(self.rect()).subtracted(QRegion(selected)))
            painter.fillRect(self._display_rect, QColor(0, 0, 0, 135))
            painter.restore()
            painter.setPen(QPen(QColor("#59a8ff"), 3))
            painter.drawRect(selected)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        source = self._to_source(event.position().toPoint())
        if source is None:
            return
        self._origin = source
        self._selection = QRect(source, source)
        self._dragging = True
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._dragging:
            return
        source = self._to_source(event.position().toPoint())
        if source is not None:
            self._selection = QRect(self._origin, source).normalized()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        source = self._to_source(event.position().toPoint())
        if source is not None:
            self._selection = QRect(self._origin, source).normalized()
        crop = self.selected_crop()
        if crop is None:
            self._selection = QRect()
        self.cropChanged.emit(crop)
        self.update()
