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
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from .audio import CHUNK_MS, MicrophoneStream
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
    vad_finalize = Signal()

    def __init__(self, config: Config, stt, embedder, debug: bool = False) -> None:
        super().__init__()
        self.logger = logging.getLogger("voice_cmds.app")
        self.config = config
        self.stt = stt
        self.embedder = embedder
        self.debug = debug
        self.state: AppState = AppState.IDLE
        self._partial_text = ""
        # VAD (silence auto-stop) state, driven from the audio callback thread
        self._vad_silence_ms = 0.0
        self._vad_floor = 0.004  # adaptive ambient noise floor (RMS)

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
        try:
            self.hotkey.start()
        except Exception as e:
            self.logger.exception("Hotkey registration failed: %s", e)
            QMessageBox.warning(
                None,
                "voice-cmds — 热键无效",
                f"无法注册热键。请打开托盘 → 设置 → 通用 修正配置。\n\n{e}",
            )

    def _connect(self) -> None:
        self.hotkey.start_pressed.connect(self.on_start)
        self.hotkey.stop_pressed.connect(self.on_stop)
        self.hotkey.cancel_pressed.connect(self.on_cancel)
        self.partial_text.connect(self._on_partial)
        # final_text_ready is emitted from a worker thread; AutoConnection
        # delivers it on the main thread because this QObject lives there.
        self.final_text_ready.connect(self._dispatch)
        self.reset_to_idle.connect(self._to_idle)
        # VAD fires from the audio callback thread; queued to the main thread.
        self.vad_finalize.connect(self._finalize_and_process)
        self.tray.settings_requested.connect(self._open_settings)
        self.tray.help_requested.connect(self._show_help)
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
        self._vad_silence_ms = 0.0
        self._vad_floor = 0.004
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
    def _on_audio_chunk(self, samples: np.ndarray, rms: float) -> None:
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
        if self.config.settings.get("stop_mode") == "vad":
            self._update_vad(rms)

    def _update_vad(self, rms: float) -> None:
        """Silence auto-stop: 0.5 s (configurable) of quiet after speech ends.

        Runs on the audio callback thread. The ambient noise floor adapts
        slowly so a noisy room doesn't keep the VAD permanently "speech".
        """
        # Track quiet RMS as the ambient floor
        if rms < self._vad_floor * 5:
            self._vad_floor = 0.95 * self._vad_floor + 0.05 * rms
        speech = rms > max(0.012, 3.0 * self._vad_floor)
        if speech:
            self._vad_silence_ms = 0.0
            return
        self._vad_silence_ms += CHUNK_MS
        vad_ms = int(self.config.settings.get("vad_silence_ms", 500))
        # Only auto-stop once something was actually recognized; an empty
        # partial means the user hasn't spoken yet — keep listening.
        if self._partial_text and self._vad_silence_ms >= vad_ms:
            self._vad_silence_ms = 0.0
            self.logger.info(
                "VAD: %d ms silence after %r — auto-stopping",
                vad_ms, self._partial_text,
            )
            self.vad_finalize.emit()

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
        """Phase 1 of completion: match now, show the result text, then
        execute after ui.result_text_ms (so the user can confirm what was
        recognized before the command actually fires)."""
        self.logger.warning("App: _dispatch called with %r", text)
        try:
            result = self.matcher.match(text)
        except Exception as e:
            self.logger.exception("Matcher error: %s", e)
            result = None
        if not result:
            self.logger.warning("No command matched for %r", text)
            self._finish_with_result(text, ok=False, result=None)
            return
        self.logger.warning(
            "Matched: trigger=%r kind=%s layer=%s score=%.2f",
            result.command.trigger, result.command.kind, result.layer, result.score,
        )
        self._finish_with_result(text, ok=True, result=result)

    def _finish_with_result(self, text: str, ok: bool, result) -> None:
        """Show the recognized text for result_text_ms, then execute.

        Green = matched (executes after the pause); red = no match.
        """
        if not text.strip():
            # Nothing recognized at all — skip the text phase entirely.
            self.overlay.show_error()
            self._reset_after_done()
            return
        self.overlay.show_result_text(text, ok=ok)
        ms = int(self.config.settings.get("ui", {}).get("result_text_ms", 1000))
        if ms <= 0:
            self._execute_then_done(text, result)
            return
        QTimer.singleShot(
            ms, lambda: self._execute_then_done(text, result)
        )

    def _execute_then_done(self, text: str, result) -> None:
        """Phase 2: execute the matched command and show the ✓/✗ circle."""
        if result is None:
            self.overlay.show_error()
            self._reset_after_done()
            return
        try:
            self.executor.execute(result)
        except Exception as e:
            self.logger.exception("Executor error: %s", e)
            self.tray.notify("voice-cmds — 执行失败", str(e))
            self.overlay.show_error()
            self._reset_after_done()
            return
        # Only announce fuzzy matches — a balloon for every literal
        # command (media keys etc.) would be noise.
        if result.layer != "literal":
            self.tray.notify(
                "voice-cmds",
                f"“{text}” → 已执行：{result.command.trigger}",
                msecs=3000,
            )
        self.overlay.show_success()
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
    def _show_help(self) -> None:
        try:
            box = QMessageBox()
            box.setWindowTitle("voice-cmds — 帮助")
            box.setTextFormat(Qt.TextFormat.RichText)
            box.setText(self.matcher.help_text())
            box.exec()
        except Exception:
            self.logger.exception("Failed to show help dialog")

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
