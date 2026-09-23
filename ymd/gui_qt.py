import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ymd import core
from ymd.downloader import DownloadOptions, DownloadProgress, DownloadState, Downloader
from ymd.settings import Profile, SettingsManager


class DownloadThread(QThread):
    progress = pyqtSignal(object)  # DownloadProgress
    finished = pyqtSignal()

    def __init__(self, downloader: Downloader, urls: list[str]):
        super().__init__()
        self.downloader = downloader
        self.urls = urls

    def run(self):
        self.downloader.download(self.urls, self._on_progress)
        self.finished.emit()

    def _on_progress(self, progress: DownloadProgress):
        self.progress.emit(progress)


class ProfileDialog(QDialog):
    def __init__(self, parent=None, name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Новый профиль" if not name else "Переименовать профиль")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Имя профиля:"))
        self.name_edit = QLineEdit(name)
        layout.addWidget(self.name_edit)
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def get_name(self) -> str:
        return self.name_edit.text().strip()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Яндекс Музыка — Загрузчик")
        self.setMinimumSize(700, 600)

        self.settings = SettingsManager()
        self.downloader: Optional[Downloader] = None
        self.download_thread: Optional[DownloadThread] = None

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # --- Profile bar ---
        profile_bar = QHBoxLayout()
        profile_bar.addWidget(QLabel("Профиль:"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        profile_bar.addWidget(self.profile_combo, 1)
        add_profile_btn = QPushButton("+")
        add_profile_btn.setFixedWidth(30)
        add_profile_btn.clicked.connect(self._add_profile)
        profile_bar.addWidget(add_profile_btn)
        del_profile_btn = QPushButton("Удалить")
        del_profile_btn.clicked.connect(self._delete_profile)
        profile_bar.addWidget(del_profile_btn)
        main_layout.addLayout(profile_bar)

        # --- Input fields ---
        input_group = QGroupBox("Параметры загрузки")
        input_layout = QVBoxLayout(input_group)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "https://music.yandex.ru/artist/... или album/... или track/... или playlists/..."
        )
        url_row.addWidget(self.url_edit, 1)
        input_layout.addLayout(url_row)

        token_row = QHBoxLayout()
        token_row.addWidget(QLabel("Токен:"))
        self.token_edit = QLineEdit()
        token_row.addWidget(self.token_edit, 1)
        get_token_btn = QPushButton("Получить токен")
        get_token_btn.clicked.connect(
            lambda: webbrowser.open(
                "https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d"
            )
        )
        token_row.addWidget(get_token_btn)
        input_layout.addLayout(token_row)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Папка:"))
        self.dir_edit = QLineEdit(".")
        dir_row.addWidget(self.dir_edit, 1)
        browse_btn = QPushButton("Обзор...")
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)
        input_layout.addLayout(dir_row)

        main_layout.addWidget(input_group)

        # --- Options ---
        options_group = QGroupBox("Настройки")
        options_layout = QVBoxLayout(options_group)

        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Качество:"))
        self.quality_low = QRadioButton("AAC 64 kbps")
        self.quality_normal = QRadioButton("AAC 192 kbps")
        self.quality_lossless = QRadioButton("FLAC")
        quality_row.addWidget(self.quality_low)
        quality_row.addWidget(self.quality_normal)
        quality_row.addWidget(self.quality_lossless)
        quality_row.addStretch()
        options_layout.addLayout(quality_row)

        opts_row1 = QHBoxLayout()
        self.embed_cover_cb = QCheckBox("Встраивать обложку")
        self.skip_existing_cb = QCheckBox("Пропускать существующие")
        self.skip_existing_cb.setChecked(True)
        opts_row1.addWidget(self.embed_cover_cb)
        opts_row1.addWidget(self.skip_existing_cb)
        opts_row1.addStretch()
        options_layout.addLayout(opts_row1)

        opts_row2 = QHBoxLayout()
        opts_row2.addWidget(QLabel("Формат текста:"))
        self.lyrics_combo = QComboBox()
        self.lyrics_combo.addItems(["Нет", "TEXT", "LRC"])
        opts_row2.addWidget(self.lyrics_combo)
        opts_row2.addWidget(QLabel("Задержка (сек):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60)
        opts_row2.addWidget(self.delay_spin)
        opts_row2.addStretch()
        options_layout.addLayout(opts_row2)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Шаблон пути:"))
        self.path_pattern_edit = QLineEdit(str(core.DEFAULT_PATH_PATTERN))
        path_row.addWidget(self.path_pattern_edit, 1)
        options_layout.addLayout(path_row)

        main_layout.addWidget(options_group)

        # --- Action buttons ---
        btn_layout = QHBoxLayout()
        self.download_btn = QPushButton("СКАЧАТЬ")
        self.download_btn.setMinimumHeight(40)
        self.download_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; font-size: 14px; }"
            "QPushButton:hover { background-color: #45a049; }"
            "QPushButton:disabled { background-color: #888; }"
        )
        self.download_btn.clicked.connect(self._start_download)
        btn_layout.addWidget(self.download_btn)

        self.pause_btn = QPushButton("Пауза")
        self.pause_btn.setMinimumHeight(40)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_layout.addWidget(self.pause_btn)

        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { background-color: #f44336; color: white; }"
            "QPushButton:hover { background-color: #d32f2f; }"
            "QPushButton:disabled { background-color: #888; }"
        )
        self.stop_btn.clicked.connect(self._stop_download)
        btn_layout.addWidget(self.stop_btn)

        main_layout.addLayout(btn_layout)

        # --- Log area ---
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        main_layout.addWidget(self.log_area, 1)

        # --- Progress bar ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)

        # --- Status bar ---
        self.status_label = QLabel("Готово")
        main_layout.addWidget(self.status_label)

        self.error_label = QLabel("Ошибок: 0")
        main_layout.addWidget(self.error_label)

        self.queue_label = QLabel("")
        main_layout.addWidget(self.queue_label)

        # Load profiles
        self._load_profiles()

    def _load_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for name in self.settings.list_profiles():
            self.profile_combo.addItem(name)
        active = self.settings.get_active_profile_name()
        idx = self.profile_combo.findText(active)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)
        self._apply_profile(self.settings.get_active_profile())

    def _apply_profile(self, profile: Profile):
        self.token_edit.setText(profile.token)
        self.dir_edit.setText(profile.output_dir)
        self.path_pattern_edit.setText(profile.path_pattern)
        self.embed_cover_cb.setChecked(profile.embed_cover)
        self.skip_existing_cb.setChecked(profile.skip_existing)
        self.delay_spin.setValue(profile.delay)

        if profile.quality == 0:
            self.quality_low.setChecked(True)
        elif profile.quality == 1:
            self.quality_normal.setChecked(True)
        else:
            self.quality_lossless.setChecked(True)

        lyrics_map = {"none": 0, "text": 1, "lrc": 2}
        self.lyrics_combo.setCurrentIndex(lyrics_map.get(profile.lyrics_format, 0))

    def _collect_profile(self, name: str) -> Profile:
        quality = 0
        if self.quality_normal.isChecked():
            quality = 1
        elif self.quality_lossless.isChecked():
            quality = 2

        lyrics_values = ["none", "text", "lrc"]

        return Profile(
            name=name,
            token=self.token_edit.text(),
            output_dir=self.dir_edit.text(),
            quality=quality,
            lyrics_format=lyrics_values[self.lyrics_combo.currentIndex()],
            embed_cover=self.embed_cover_cb.isChecked(),
            skip_existing=self.skip_existing_cb.isChecked(),
            path_pattern=self.path_pattern_edit.text(),
            delay=self.delay_spin.value(),
        )

    @pyqtSlot(str)
    def _on_profile_changed(self, name: str):
        if not name:
            return
        self.settings.set_active_profile(name)
        self._apply_profile(self.settings.get_profile(name))

    def _add_profile(self):
        dlg = ProfileDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            name = dlg.get_name()
            if name and name not in self.settings.list_profiles():
                profile = self._collect_profile(name)
                self.settings.save_profile(profile)
                self._load_profiles()

    def _delete_profile(self):
        name = self.profile_combo.currentText()
        if name == "default":
            QMessageBox.information(self, "Инфо", "Профиль default нельзя удалить")
            return
        self.settings.delete_profile(name)
        self._load_profiles()

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if d:
            self.dir_edit.setText(d)

    def _get_quality(self) -> int:
        if self.quality_normal.isChecked():
            return 1
        if self.quality_lossless.isChecked():
            return 2
        return 0

    def _start_download(self):
        url_text = self.url_edit.text().strip()
        if not url_text:
            QMessageBox.warning(self, "Ошибка", "Введите URL")
            return

        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "Ошибка", "Введите токен")
            return

        # Save current profile
        name = self.profile_combo.currentText()
        profile = self._collect_profile(name)
        self.settings.save_profile(profile)

        # Parse URLs (one per line or comma-separated)
        urls = [
            u.strip()
            for u in url_text.replace(",", "\n").split("\n")
            if u.strip()
        ]

        lyrics_map = {"none": core.LyricsFormat.NONE, "text": core.LyricsFormat.TEXT, "lrc": core.LyricsFormat.LRC}

        options = DownloadOptions(
            token=token,
            quality=self._get_quality(),
            output_dir=Path(self.dir_edit.text()),
            path_pattern=Path(self.path_pattern_edit.text()),
            skip_existing=self.skip_existing_cb.isChecked(),
            embed_cover=self.embed_cover_cb.isChecked(),
            lyrics_format=lyrics_map.get(profile.lyrics_format, core.LyricsFormat.NONE),
            delay=self.delay_spin.value(),
        )

        self.downloader = Downloader(options)
        self.download_thread = DownloadThread(self.downloader, urls)
        self.download_thread.progress.connect(self._on_progress)
        self.download_thread.finished.connect(self._on_finished)

        self.log_area.clear()
        self.progress_bar.setValue(0)
        self.error_label.setText("Ошибок: 0")
        self.queue_label.setText(f"Очередь: {len(urls)} URL")

        self.download_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.status_label.setText("Загрузка...")

        self.download_thread.start()

    def _toggle_pause(self):
        if self.downloader is None:
            return
        if self.downloader.state == DownloadState.DOWNLOADING:
            self.downloader.pause()
            self.pause_btn.setText("Продолжить")
            self.status_label.setText("Пауза")
        elif self.downloader.state == DownloadState.PAUSED:
            self.downloader.resume()
            self.pause_btn.setText("Пауза")
            self.status_label.setText("Загрузка...")

    def _stop_download(self):
        if self.downloader:
            self.downloader.stop()
            self.status_label.setText("Остановка...")

    @pyqtSlot(object)
    def _on_progress(self, progress: DownloadProgress):
        codec_info = ""
        if progress.codec:
            codec_info = f"[{progress.codec}"
            if progress.bitrate > 0:
                codec_info += f" {progress.bitrate}kbps"
            codec_info += "] "

        prefix = ""
        if progress.total:
            prefix = f"[{progress.current}/{progress.total}] "
            pct = int(progress.current / progress.total * 100)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{progress.current}/{progress.total} ({pct}%)")

        if progress.status == "downloading":
            self.log_area.append(f"{prefix}{codec_info}Загружается {progress.file_path}")
        elif progress.status == "done":
            self.log_area.append(f"{prefix}{codec_info}Готово: {progress.file_path}")
        elif progress.status == "skipped":
            self.log_area.append(f"{prefix}Пропущен: {progress.file_path}")
        elif progress.status == "error":
            if progress.track_title:
                self.log_area.append(
                    f"{prefix}Ошибка: {progress.track_artist} - {progress.track_title}"
                )
            else:
                self.log_area.append(f"{prefix}Ошибка: {progress.file_path}")

        self.error_label.setText(f"Ошибок: {progress.errors}")
        if progress.queue_remaining > 0:
            self.queue_label.setText(f"Очередь: {progress.queue_remaining} URL осталось")
        else:
            self.queue_label.setText("Очередь: последний URL")

    def _on_finished(self):
        self.download_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setText("Пауза")
        self.status_label.setText("Готово")
        self.progress_bar.setValue(100)
        self.queue_label.setText("")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
