"""voice-cmds entry point.

Usage:
    python main.py            # normal run
    python main.py --debug    # verbose logs to ./logs/

Boot flow:
    1. QApplication + SIGINT plumbing
    2. SplashWindow shown immediately
    3. Worker thread: load config, download/init STT model, build app
    4. When ready, splash hides and tray-resident app runs
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import traceback

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

import keyboard as kb

from voice_cmds.config import Config
from voice_cmds.logger import setup_logger
from voice_cmds.ui.splash import SplashWindow


class _Bootstrap(QObject):
    """Owns the splash + app lifecycle so it survives across threads."""

    ready = Signal(object, object)  # (StreamingSTT, SentenceTransformer)
    failed = Signal(str)            # error traceback

    def __init__(self, config: Config, debug: bool) -> None:
        super().__init__()
        self.config = config
        self.debug = debug
        self.splash = SplashWindow()
        self.app_holder = None  # holds VoiceCmdsApp once built
        self.ready.connect(self._on_ready)
        self.failed.connect(self._on_failed)

    def start(self) -> None:
        self.splash.show()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            self.splash.set_status("正在加载配置…")
            self.splash.set_indeterminate()

            # 1. STT
            self.splash.set_status("正在准备语音识别模型…")
            from voice_cmds.stt import StreamingSTT
            stt = StreamingSTT.prepare(
                max_chars=self.config.settings["max_chars"],
                status_cb=self.splash.set_status,
                progress_cb=self.splash.set_progress,
            )

            # 2. Embedder for semantic command matching
            self.splash.set_indeterminate()
            from voice_cmds.matcher import prepare_embedder
            embedder = prepare_embedder(status_cb=self.splash.set_status)

            self.ready.emit(stt, embedder)
        except Exception:
            tb = traceback.format_exc()
            self.failed.emit(tb)

    @Slot(object, object)
    def _on_ready(self, stt, embedder) -> None:
        if self.splash is not None:
            self.splash.set_status("启动中…")
            self.splash.set_progress(1, 1)
        # Brief delay so the user can see the final status, then launch.
        QTimer.singleShot(150, lambda: self._launch(stt, embedder))

    def _launch(self, stt, embedder) -> None:
        log = logging.getLogger("voice_cmds.bootstrap")
        log.warning("_launch: tearing down splash")
        # Tear down splash FIRST. If app construction is slow / pumps events,
        # the splash would otherwise outlive _launch and stay visible.
        self._destroy_splash()
        QApplication.processEvents()
        log.warning("_launch: constructing VoiceCmdsApp")
        try:
            from voice_cmds.app import VoiceCmdsApp
            self.app_holder = VoiceCmdsApp(
                self.config, stt, embedder, debug=self.debug
            )
        except Exception:
            log.exception("VoiceCmdsApp construction failed")
            QMessageBox.critical(
                None, "voice-cmds 启动失败", traceback.format_exc()
            )
            QApplication.quit()
            os._exit(1)
        log.warning("_launch: ready")

    def _destroy_splash(self) -> None:
        if self.splash is None:
            return
        try:
            self.splash.hide()
            self.splash.close()
            self.splash.deleteLater()
        except Exception:
            pass
        self.splash = None

    @Slot(str)
    def _on_failed(self, tb: str) -> None:
        self._destroy_splash()
        QMessageBox.critical(
            None,
            "voice-cmds 启动失败",
            f"无法初始化语音识别模型。\n\n{tb}",
        )
        try:
            kb.unhook_all()
        except Exception:
            pass
        QApplication.quit()
        os._exit(1)


def _install_sigint(app: QApplication) -> None:
    """Make Ctrl+C in the terminal actually quit Qt + the keyboard hook thread."""
    def _handler(*_):
        try:
            kb.unhook_all()
        except Exception:
            pass
        app.quit()
        os._exit(0)

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)

    # Wake the Python interpreter every 200ms so signals can be delivered while
    # Qt's C++ event loop is running.
    keepalive = QTimer()
    keepalive.start(200)
    keepalive.timeout.connect(lambda: None)
    app._sigint_keepalive = keepalive  # prevent GC


def main() -> int:
    parser = argparse.ArgumentParser(prog="voice-cmds")
    parser.add_argument("--debug", action="store_true", help="enable file logging in ./logs/")
    args = parser.parse_args()

    logger = setup_logger(args.debug)
    logger.info("voice-cmds starting (debug=%s)", args.debug)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    _install_sigint(app)

    config = Config()
    boot = _Bootstrap(config, debug=args.debug)
    boot.start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
