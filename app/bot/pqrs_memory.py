from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re


@dataclass
class PQRSDraft:
    text: str
    created_at: str
    updated_at: str
    status: str = "idle"
    irrespetuosa: bool = False
    timeout_task: asyncio.Task | None = field(default=None, repr=False)

    def cancel_timeout(self) -> None:
        if self.timeout_task is not None and not self.timeout_task.done():
            self.timeout_task.cancel()
            self.timeout_task = None


class PQRSMemoryStore:
    def __init__(self) -> None:
        self._drafts: dict[int, PQRSDraft] = {}

    @staticmethod
    def _sanitize_fragment(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        ignored_phrases = (
            "señores",
            "senores",
            "atentamente",
            "por medio de la presente me permito presentar",
            "por medio de la presente",
            "agradezco la atención brindada",
            "quedo atento",
            "quedo atenta",
        )

        lines: list[str] = []
        for line in raw.splitlines():
            clean = " ".join(line.strip().split())
            if not clean:
                continue
            lower = clean.lower()
            if "[" in clean and "]" in clean:
                continue
            if any(phrase in lower for phrase in ignored_phrases):
                continue
            lines.append(clean)

        joined = "\n".join(lines).strip()
        if not joined:
            return ""
        return re.sub(r"\n{3,}", "\n\n", joined)

    def get(self, chat_id: int) -> PQRSDraft | None:
        return self._drafts.get(chat_id)

    def set(
        self,
        chat_id: int,
        text: str,
        status: str = "idle",
        irrespetuosa: bool = False,
        timeout_task: asyncio.Task | None = None,
    ) -> PQRSDraft:
        clean = self._sanitize_fragment(text)
        if not clean:
            self.clear(chat_id)
            now = datetime.now(timezone.utc).isoformat()
            return PQRSDraft(text="", created_at=now, updated_at=now)
        now = datetime.now(timezone.utc).isoformat()
        draft = PQRSDraft(
            text=clean,
            created_at=now,
            updated_at=now,
            status=status,
            irrespetuosa=irrespetuosa,
            timeout_task=timeout_task,
        )
        self._drafts[chat_id] = draft
        return draft

    def update_text(self, chat_id: int, text: str) -> PQRSDraft | None:
        draft = self._drafts.get(chat_id)
        if draft is None:
            return None
        clean = self._sanitize_fragment(text)
        if not clean:
            self.clear(chat_id)
            return None
        now = datetime.now(timezone.utc).isoformat()
        updated = PQRSDraft(
            text=clean,
            created_at=draft.created_at,
            updated_at=now,
            status=draft.status,
            irrespetuosa=draft.irrespetuosa,
            timeout_task=draft.timeout_task,
        )
        self._drafts[chat_id] = updated
        return updated

    def clear(self, chat_id: int) -> None:
        draft = self._drafts.pop(chat_id, None)
        if draft is not None:
            draft.cancel_timeout()
