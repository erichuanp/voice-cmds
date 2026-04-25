"""Coordinator: wires hotkey → audio → STT → overlay → matcher → executor."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from enum import Enum, auto

import keyboard as kb
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from .audio import MicrophoneStream
from .config import Config
from .executor import CommandExecutor
from .hotkey import HotkeyManager
from .matcher import CommandMatcher
from .ui.overlay import OverlayWindow
from .ui.settings import SettingsDialog
from .ui.tray import TrayIcon


class AppState(Enum):
    IDLE = auto()
    RECORDING = auto()
    PROCESSING = auto()


class VoiceCmdsApp(QObject):
    """All cross-thread events go through Signals so UI runs on Qt main thread."""

    partial_text = Signal(str)
    final_text_ready = Signal(str)
    reset_to_idle = Signal()

    def __init__(self, config: Config, stt, embedder, debug: bool = False) -> None:
        super().__init__()
        self.logger = logging.getLogger("voice_cmds.app")
        self.config = config
        self.stt = stt
        self.embedder = embedder
        self.debug = debug
        self.state: AppState = AppState.IDLE
        self._partial_text = ""

        self.matcher = CommandMatcher(self.config, self.embedder)
        self.executor = CommandExecutor(self.config, self.logger)
        self.audio = MicrophoneStream()
        self.overlay = OverlayWindow(self.config.settings)
        self.tray = TrayIcon(self)
        self.hotkey = HotkeyManager(
            self.config.settings["hotkey"]["start"],
            self.config.settings["hotkey"]["stop"],
            self.config.settings["hotkey"]["cancel"],
        )

        self._connect()
        self.hotkey.start()

    def _connect(self) -> None:
        self.hotkey.start_pressed.connect(self.on_start)
        self.hotkey.stop_pressed.connect(self.on_stop)
        self.hotkey.cancel_pressed.connect(self.on_cancel)
        self.partial_text.connect(self._on_partial)
        # final_text_ready is emitted from a worker thread; AutoConnection
        # delivers it on the main thread because this QObject lives there.
        self.final_text_ready.connect(self._dispatch)
        self.reset_to_idle.connect(self._to_idle)
        self.tray.settings_requested.connect(self._open_settings)
        self.tray.reload_requested.connect(self._reload_config)
        self.tray.exit_requested.connect(self.shutdown)

    # --- hotkey handlers ---
    @Slot()
    def on_start(self) -> None:
        self.logger.warning("App: on_start (state=%s)", self.state.name)
        if self.state != AppState.IDLE:
            return
        self.state = AppState.RECORDING
        self.hotkey.set_recording(True)
        self._partial_text = ""
        try:
            self.stt.reset()
        except Exception:
            self.logger.exception("STT reset failed")
        self.overlay.show_recording()
        self.audio.start(self._on_audio_chunk)

    @Slot()
    def on_stop(self) -> None:
        if self.state != AppState.RECORDING:
            return
        self._finalize_and_process()

    @Slot()
    def on_cancel(self) -> None:
        if self.state != AppState.RECORDING:
            return
        self.logger.info("Cancel pressed")
        self.audio.stop()
        self.hotkey.set_recording(False)
        self.state = AppState.IDLE
        self.overlay.hide_overlay()

    # --- audio thread callback ---
    def _on_audio_chunk(self, samples: np.ndarray) -> None:
        if self.state != AppState.RECORDING or self.stt is None:
            return
        try:
            partial = self.stt.feed(samples)
        except Exception as e:
            self.logger.exception("STT feed error: %s", e)
            return
        if partial != self._partial_text:
            self._partial_text = partial
            self.partial_text.emit(partial)

    @Slot(str)
    def _on_partial(self, text: str) -> None:
        self.overlay.update_text(text)
        if self.stt and self.stt.at_limit(text):
            self.logger.info("Char limit reached, auto-stopping")
            self._finalize_and_process()

    # --- finalize + dispatch ---
    def _finalize_and_process(self) -> None:
        if self.state != AppState.RECORDING:
            return
        self.state = AppState.PROCESSING
        self.hotkey.set_recording(False)
        self.audio.stop()
        self.overlay.show_processing()

        def worker():
            try:
                final_text = self.stt.finalize() if self.stt else ""
            except Exception as e:
                self.logger.exception("STT finalize error: %s", e)
                final_text = self._partial_text
            self.logger.warning("Final transcript: %r", final_text)
            # Cross-thread: emit signal; AutoConnection -> queued on main thread.
            self.final_text_ready.emit(final_text)

        threading.Thread(target=worker, daemon=True).start()

    @Slot(str)
    def _dispatch(self, text: str) -> None:
        self.logger.warning("App: _dispatch called with %r", text)
        try:
            result = self.matcher.match(text)
        except Exception as e:
            self.logger.exception("Matcher error: %s", e)
            result = None
        if not result:
            self.logger.warning("No command matched for %r", text)
            self.overlay.show_error()
            self._reset_after_done()
            return
        self.logger.warning(
            "Matched: trigger=%r kind=%s layer=%s score=%.2f",
            result.command.trigger, result.command.kind, result.layer, result.score,
        )
        try:
            self.executor.execute(result)
            self.overlay.show_success()
        except Exception as e:
            self.logger.exception("Executor error: %s", e)
            self.overlay.show_error()
        self._reset_after_done()

    def _reset_after_done(self) -> None:
        # 2.1s after success/error overlay is shown, return state to IDLE
        QTimer.singleShot(2100, self._to_idle)

    @Slot()
    def _to_idle(self) -> None:
        self.state = AppState.IDLE

    # --- tray actions ---
    @Slot()
    def _open_settings(self) -> None:
        d = SettingsDialog(self.config, debug=self.debug)
        # Saving from the dialog triggers a full restart so all changes
        # (hotkeys, new commands needing fresh embedding cache, autostart)
        # take effect cleanly.
        d.config_changed.connect(self._restart_after_save)
        d.exec()

    @Slot()
    def _reload_config(self) -> None:
        # Tray "重新加载配置" — soft reload only (no restart).
        self.config.reload()
        self.matcher.reload()
        self.logger.info("Config reloaded (soft)")
        QMessageBox.information(
            None, "voice-cmds", "配置已重新加载。\n（修改热键需要重启程序）"
        )

    @Slot()
    def _restart_after_save(self) -> None:
        QMessageBox.information(None, "voice-cmds", "配置已保存，程序将自动重启以应用更改。")
        self.restart()

    def restart(self) -> None:
        """Spawn a fresh instance with the same args, then exit."""
        # When frozen (PyInstaller exe), sys.executable IS the launcher, so
        # don't double-include argv[0]. In source mode, sys.executable is
        # python.exe and argv[0] is main.py — both are needed.
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, *sys.argv[1:]]
        else:
            cmd = [sys.executable, *sys.argv]
        self.logger.warning("Restarting: %s", cmd)
        try:
            DETACHED = 0x00000008  # DETACHED_PROCESS
            CREATE_NEW_GROUP = 0x00000200
            subprocess.Popen(
                cmd,
                cwd=os.getcwd(),
                creationflags=DETACHED | CREATE_NEW_GROUP,
                close_fds=True,
            )
        except Exception:
            self.logger.exception("Failed to spawn replacement process")
        self.shutdown()

    @Slot()
    def shutdown(self) -> None:
        """Force-clean shutdown. Tray icons + keyboard hooks + Qt event loop."""
        self.logger.info("Shutting down")
        try:
            self.hotkey.stop()
        except Exception:
            pass
        try:
            self.audio.stop()
        except Exception:
            pass
        try:
            kb.unhook_all()
        except Exception:
            pass
        try:
            self.tray.tray.hide()
        except Exception:
            pass
        QApplication.quit()
        # keyboard library spawns a Windows hook thread that can keep the
        # process alive even after Qt's loop exits. Force the issue.
        os._exit(0)
