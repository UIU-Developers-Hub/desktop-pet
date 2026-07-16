"""Settings dialog for local/cloud Ollama routing."""

from __future__ import annotations

import config
from ai.llm_client import OllamaClient
from data.settings_store import AppSettings, normalize_provider
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    """Modal editor for persisted LLM provider and voice settings."""

    def __init__(self, llm_client: OllamaClient, parent=None):
        super().__init__(parent)
        self.llm_client = llm_client
        self.settings = llm_client.settings()
        self.setWindowTitle(f"{config.APP_NAME} Settings")
        self.setMinimumWidth(560)
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(12)

        routing_group = QGroupBox("Routing", self)
        routing_form = QFormLayout(routing_group)
        routing_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.provider_box = QComboBox(routing_group)
        self.provider_box.addItem("Auto: local, cloud if device is hot", "auto")
        self.provider_box.addItem("Local Ollama", "local")
        self.provider_box.addItem("Ollama Cloud", "cloud")
        routing_form.addRow("Default chat route", self.provider_box)

        self.cloud_fallback_box = QCheckBox("Use Ollama Cloud when local CPU/RAM is above the threshold", routing_group)
        routing_form.addRow("", self.cloud_fallback_box)

        self.timeout_box = QSpinBox(routing_group)
        self.timeout_box.setRange(2, 120)
        self.timeout_box.setSuffix(" sec")
        routing_form.addRow("Request timeout", self.timeout_box)
        root.addWidget(routing_group)

        root.addWidget(self._build_local_group())
        root.addWidget(self._build_cloud_group())
        root.addWidget(self._build_voice_group())

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.setStyleSheet(
            """
            QDialog {
                background: #1e1e24;
                color: #e2e2e5;
                font-family: "Inter", "Segoe UI Variable", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QGroupBox {
                background: #25252d;
                border: 1px solid #3d3d48;
                border-radius: 12px;
                margin-top: 14px;
                padding: 16px;
                font-weight: 700;
                color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 6px;
                color: #50d890;
            }
            QLineEdit, QComboBox, QSpinBox {
                background: #181820;
                border: 1px solid #3d3d48;
                border-radius: 8px;
                padding: 8px;
                color: #e2e2e5;
                font-weight: 400;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #50d890;
            }
            QPushButton {
                background: #50d890;
                color: #17201b;
                border: 0;
                border-radius: 8px;
                padding: 9px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #62e49f;
            }
            QPushButton#secondaryButton {
                background: #3d3d48;
                color: #e2e2e5;
                border: 1px solid #4a4a58;
            }
            QPushButton#secondaryButton:hover {
                background: #4a4a58;
            }
            QLabel#statusLabel {
                color: #9ba1a6;
            }
            QLabel#voiceHelp {
                color: #9ba1a6;
                font-size: 12px;
            }
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #3d3d48;
                background: #181820;
            }
            QCheckBox::indicator:checked {
                background: #50d890;
                border: 1px solid #50d890;
            }
            """
        )

    def _build_local_group(self) -> QGroupBox:
        group = QGroupBox("Local Ollama", self)
        layout = QFormLayout(group)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.local_url_input = QLineEdit(group)
        layout.addRow("Host", self.local_url_input)

        model_row = QHBoxLayout()
        self.local_model_box = QComboBox(group)
        self.local_model_box.setEditable(True)
        self.local_detect_button = QPushButton("Detect models", group)
        self.local_detect_button.setObjectName("secondaryButton")
        self.local_detect_button.clicked.connect(lambda: self._detect_models("local"))
        model_row.addWidget(self.local_model_box, 1)
        model_row.addWidget(self.local_detect_button)
        layout.addRow("Model", model_row)
        return group

    def _build_cloud_group(self) -> QGroupBox:
        group = QGroupBox("Ollama Cloud", self)
        layout = QFormLayout(group)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.cloud_url_input = QLineEdit(group)
        layout.addRow("Host", self.cloud_url_input)

        self.cloud_key_input = QLineEdit(group)
        self.cloud_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.cloud_key_input.setPlaceholderText("Ollama API key")
        layout.addRow("API key", self.cloud_key_input)

        model_row = QHBoxLayout()
        self.cloud_model_box = QComboBox(group)
        self.cloud_model_box.setEditable(True)
        self.cloud_detect_button = QPushButton("Detect models", group)
        self.cloud_detect_button.setObjectName("secondaryButton")
        self.cloud_detect_button.clicked.connect(lambda: self._detect_models("cloud"))
        model_row.addWidget(self.cloud_model_box, 1)
        model_row.addWidget(self.cloud_detect_button)
        layout.addRow("Model", model_row)
        return group

    def _build_voice_group(self) -> QGroupBox:
        group = QGroupBox("Voice", self)
        layout = QFormLayout(group)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.voice_enabled_box = QCheckBox("Speak pet replies using Microsoft voice", group)
        layout.addRow("", self.voice_enabled_box)

        help_label = QLabel("Uses the default Windows voice through Microsoft SAPI.", group)
        help_label.setObjectName("voiceHelp")
        help_label.setWordWrap(True)
        layout.addRow("", help_label)
        return group

    def _load_values(self) -> None:
        self._set_combo_data(self.provider_box, normalize_provider(self.settings.chat_provider))
        self.cloud_fallback_box.setChecked(self.settings.cloud_fallback_enabled)
        self.voice_enabled_box.setChecked(self.settings.voice_enabled)
        self.timeout_box.setValue(self.settings.timeout_seconds)
        self.local_url_input.setText(self.settings.local_base_url)
        self.cloud_url_input.setText(self.settings.cloud_base_url)
        self.cloud_key_input.setText(self.settings.cloud_api_key)
        self._set_model_box(self.local_model_box, [self.settings.local_model], self.settings.local_model)
        self._set_model_box(self.cloud_model_box, [self.settings.cloud_model], self.settings.cloud_model)

    def _save(self) -> None:
        settings = self._settings_from_fields()
        self.llm_client.save_settings(settings)
        self.settings = settings
        self.accept()

    def _detect_models(self, provider: str) -> None:
        """Fetch model names for a provider and repopulate its combo box."""
        settings = self._settings_from_fields()
        button = self.cloud_detect_button if provider == "cloud" else self.local_detect_button
        button.setEnabled(False)
        self.status_label.setText(f"Checking {provider} models...")
        try:
            models, error = self.llm_client.list_models(provider, settings)
        finally:
            button.setEnabled(True)
        if error:
            self.status_label.setText(error)
            return
        if provider == "cloud":
            current = self.cloud_model_box.currentText()
            self._set_model_box(self.cloud_model_box, models, current)
        else:
            current = self.local_model_box.currentText()
            self._set_model_box(self.local_model_box, models, current)
        self.status_label.setText(f"Found {len(models)} {provider} model(s).")

    def _settings_from_fields(self) -> AppSettings:
        return AppSettings(
            local_base_url=self.local_url_input.text(),
            local_model=self.local_model_box.currentText(),
            cloud_base_url=self.cloud_url_input.text(),
            cloud_model=self.cloud_model_box.currentText(),
            cloud_api_key=self.cloud_key_input.text(),
            chat_provider=self.provider_box.currentData(),
            cloud_fallback_enabled=self.cloud_fallback_box.isChecked(),
            timeout_seconds=self.timeout_box.value(),
            voice_enabled=self.voice_enabled_box.isChecked(),
        ).normalized()

    def _set_model_box(self, box: QComboBox, models: list[str], current: str) -> None:
        box.blockSignals(True)
        box.clear()
        for model in models:
            box.addItem(model)
        if current and box.findText(current) < 0:
            box.addItem(current)
        if current:
            box.setCurrentText(current)
        box.blockSignals(False)

    def _set_combo_data(self, box: QComboBox, value: str) -> None:
        index = box.findData(value)
        if index >= 0:
            box.setCurrentIndex(index)
