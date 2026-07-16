"""Ollama local/cloud client with provider routing and friendly failures."""

from __future__ import annotations

import requests

import config
from data.settings_store import AppSettings, SettingsStore, normalize_provider


class OllamaClient:
    """Thin wrapper around the Ollama chat and model-list endpoints.

    The client centralizes model routing so UI and behavior code can request
    "auto", "local", or "cloud" without duplicating cloud fallback and device
    load checks.
    """

    def __init__(
        self,
        settings_store: SettingsStore | None = None,
        base_url: str = config.OLLAMA_URL,
        model: str = config.OLLAMA_MODEL,
        timeout_seconds: int = config.LLM_TIMEOUT_SECONDS,
    ):
        self.settings_store = settings_store or SettingsStore()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def settings(self) -> AppSettings:
        """Return normalized persisted settings."""
        return self.settings_store.load()

    def save_settings(self, settings: AppSettings) -> None:
        """Persist normalized settings from the Settings dialog."""
        self.settings_store.save(settings)

    def chat(
        self,
        messages: list[dict[str, str]],
        provider: str | None = None,
        device_snapshot=None,
    ) -> str:
        """Send a non-streaming chat request to the selected Ollama provider."""
        settings = self.settings()
        selected = normalize_provider(provider or settings.chat_provider)
        route = self.resolve_provider(selected, device_snapshot, settings)
        if route == "cloud" and not self.cloud_ready(settings):
            return "Ollama Cloud is not configured yet. Add an API key and cloud model in Settings."
        if (
            selected == "auto"
            and route == "local"
            and device_snapshot is not None
            and not getattr(device_snapshot, "safe_for_llm", True)
        ):
            return (
                "Local CPU/RAM is high and Ollama Cloud fallback is not configured. "
                "Add an Ollama API key in Settings or switch the chat route."
            )

        if route == "cloud":
            return self._chat_endpoint(
                messages,
                base_url=settings.cloud_base_url,
                model=settings.cloud_model,
                timeout_seconds=settings.timeout_seconds,
                api_key=settings.cloud_api_key,
                provider_name="Ollama Cloud",
            )

        return self._chat_endpoint(
            messages,
            base_url=settings.local_base_url,
            model=settings.local_model,
            timeout_seconds=settings.timeout_seconds,
            api_key="",
            provider_name="local Ollama",
        )

    def resolve_provider(
        self,
        provider: str | None = None,
        device_snapshot=None,
        settings: AppSettings | None = None,
    ) -> str:
        """Resolve `auto` into `local` or `cloud` for the current device state."""
        settings = settings or self.settings()
        selected = normalize_provider(provider or settings.chat_provider)
        if selected in {"local", "cloud"}:
            return selected
        if (
            config.LLM_DEVICE_GATING
            and settings.cloud_fallback_enabled
            and self.cloud_ready(settings)
            and device_snapshot is not None
            and not getattr(device_snapshot, "safe_for_llm", True)
        ):
            return "cloud"
        return "local"

    def cloud_ready(self, settings: AppSettings | None = None) -> bool:
        """Return true when cloud routing has enough settings to make a call."""
        settings = settings or self.settings()
        return bool(settings.cloud_api_key and settings.cloud_model and settings.cloud_base_url)

    def list_models(
        self,
        provider: str,
        settings: AppSettings | None = None,
    ) -> tuple[list[str], str | None]:
        """Fetch available models for a provider.

        Returns a `(models, error)` tuple so the Settings dialog can display
        user-facing errors without raising out of the UI flow.
        """
        settings = settings or self.settings()
        selected = normalize_provider(provider)
        if selected == "cloud":
            return self._list_models_endpoint(
                settings.cloud_base_url,
                settings.timeout_seconds,
                settings.cloud_api_key,
                "Ollama Cloud",
            )
        return self._list_models_endpoint(
            settings.local_base_url,
            settings.timeout_seconds,
            "",
            "local Ollama",
        )

    def _chat_endpoint(
        self,
        messages: list[dict[str, str]],
        base_url: str,
        model: str,
        timeout_seconds: int,
        api_key: str,
        provider_name: str,
    ) -> str:
        """Call one Ollama-compatible `/api/chat` endpoint."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        headers = self._headers(api_key)
        try:
            response = requests.post(
                api_url(base_url, "chat"),
                json=payload,
                headers=headers,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.HTTPError as exc:
            response = exc.response
            detail = self._read_error_detail(response)
            if response is not None and response.status_code == 404:
                return (
                    f'{provider_name} does not have the configured model "{model}". '
                    "Open Settings, detect models, and choose one that is available."
                )
            if response is not None and response.status_code in {401, 403}:
                return f"{provider_name} rejected the API key or access token."
            if detail:
                return f'{provider_name} returned an error for "{model}": {detail}'
            status = response.status_code if response is not None else "unknown"
            return f'{provider_name} returned HTTP {status} for "{model}".'
        except requests.Timeout:
            return (
                f"{provider_name} did not answer within {timeout_seconds} seconds. "
                "Increase the request timeout in Settings or try again after the model finishes loading."
            )
        except requests.RequestException:
            return (
                f"I cannot reach {provider_name} yet. I am still here, but the model service "
                "may be offline or unreachable."
            )
        except ValueError:
            return f"{provider_name} answered, but I could not read the response cleanly."
        message = data.get("message", {})
        content = message.get("content", "")
        return content.strip() or f"I got an empty response from {provider_name}."

    def _list_models_endpoint(
        self,
        base_url: str,
        timeout_seconds: int,
        api_key: str,
        provider_name: str,
    ) -> tuple[list[str], str | None]:
        """Call one Ollama-compatible `/api/tags` endpoint."""
        try:
            response = requests.get(
                api_url(base_url, "tags"),
                headers=self._headers(api_key),
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.HTTPError as exc:
            response = exc.response
            detail = self._read_error_detail(response)
            if response is not None and response.status_code in {401, 403}:
                return [], f"{provider_name} rejected the API key or access token."
            if detail:
                return [], f"{provider_name} returned an error: {detail}"
            status = response.status_code if response is not None else "unknown"
            return [], f"{provider_name} returned HTTP {status}."
        except requests.Timeout:
            return [], f"{provider_name} did not answer within {timeout_seconds} seconds."
        except requests.RequestException:
            return [], f"I cannot reach {provider_name}."
        except ValueError:
            return [], f"{provider_name} answered, but I could not read the model list."

        models = data.get("models", [])
        names = sorted(
            str(model.get("name", "")).strip()
            for model in models
            if isinstance(model, dict) and str(model.get("name", "")).strip()
        )
        if not names:
            return [], f"{provider_name} did not return any models."
        return names, None

    def _headers(self, api_key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = str(api_key or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _read_error_detail(self, response: requests.Response | None) -> str:
        if response is None:
            return ""
        try:
            data = response.json()
        except ValueError:
            return response.text.strip()
        return str(data.get("error", "")).strip()


def api_url(base_url: str, endpoint: str) -> str:
    """Return a normalized Ollama API URL for a base host and endpoint name."""
    base = str(base_url or config.OLLAMA_URL).strip().rstrip("/")
    if not base.endswith("/api"):
        base = f"{base}/api"
    return f"{base}/{endpoint.lstrip('/')}"
