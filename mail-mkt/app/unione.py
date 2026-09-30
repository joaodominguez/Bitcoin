from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .config import Config


class UnioneError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass
class SendResult:
    ok: bool
    job_id: str | None
    emails: list[dict]
    raw: dict
    error: str | None = None


class UnioneClient:
    """Thin client for UniOne transactional Web API v1."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ):
        self.api_key = api_key if api_key is not None else Config.UNIONE_API_KEY
        self.base_url = (base_url or Config.UNIONE_BASE_URL).rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key and Config.UNIONE_FROM_EMAIL)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise UnioneError("UNIONE_API_KEY não configurada")
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": self.api_key,
        }

    def post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = requests.post(url, json=body, headers=self._headers(), timeout=self.timeout)
        except requests.RequestException as exc:
            raise UnioneError(f"Erro de rede UniOne: {exc}") from exc
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text}
        if not resp.ok:
            raise UnioneError(
                f"UniOne HTTP {resp.status_code}: {data}",
                status_code=resp.status_code,
                payload=data,
            )
        if isinstance(data, dict) and data.get("status") not in (None, "success"):
            # Some endpoints return status=success; others omit it
            if data.get("status") == "error" or data.get("code"):
                raise UnioneError(
                    f"UniOne erro: {data}",
                    status_code=resp.status_code,
                    payload=data,
                )
        return data if isinstance(data, dict) else {"data": data}

    def ping(self) -> dict:
        return self.post("system/ping.json", {})

    def send_email(
        self,
        *,
        recipients: list[dict],
        subject: str,
        html: str,
        plaintext: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
        reply_to: str | None = None,
        campaign_id: str | int | None = None,
        idempotence_key: str | None = None,
        unsubscribe_url: str | None = None,
        tags: list[str] | None = None,
    ) -> SendResult:
        if not recipients:
            raise UnioneError("Sem destinatários")
        if len(recipients) > 500:
            raise UnioneError("UniOne aceita no máximo 500 recipients por pedido")

        from_email = from_email or Config.UNIONE_FROM_EMAIL
        if not from_email:
            raise UnioneError("UNIONE_FROM_EMAIL não configurado")

        message: dict[str, Any] = {
            "recipients": recipients,
            "subject": subject,
            "body": {"html": html},
            "from_email": from_email,
            "from_name": from_name or Config.UNIONE_FROM_NAME or "mail-mkt",
            "template_engine": "velocity",
            "track_links": 1 if Config.UNIONE_TRACK_LINKS else 0,
            "track_read": 1 if Config.UNIONE_TRACK_READ else 0,
            "skip_unsubscribe": 0,
        }
        if plaintext:
            message["body"]["plaintext"] = plaintext
        reply = reply_to or Config.UNIONE_REPLY_TO
        if reply:
            message["reply_to"] = reply
        if tags:
            message["tags"] = tags[:4]
        if campaign_id is not None:
            message["global_metadata"] = {"campaign_id": str(campaign_id)}
        if idempotence_key:
            message["idempotence_key"] = idempotence_key[:64]
        options: dict[str, Any] = {}
        if unsubscribe_url:
            options["unsubscribe_url"] = unsubscribe_url
        if options:
            message["options"] = options

        try:
            raw = self.post("email/send.json", {"message": message})
        except UnioneError as exc:
            return SendResult(ok=False, job_id=None, emails=[], raw=exc.payload or {}, error=str(exc))

        emails = raw.get("emails") or raw.get("failed_emails") or []
        if not isinstance(emails, list):
            emails = []
        ok = raw.get("status") == "success" or bool(raw.get("job_id"))
        return SendResult(
            ok=ok,
            job_id=raw.get("job_id"),
            emails=emails,
            raw=raw,
            error=None if ok else str(raw),
        )
