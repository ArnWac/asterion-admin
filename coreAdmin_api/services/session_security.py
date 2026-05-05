"""
In-memory session tracking — records active JTIs so sessions can be listed
and selectively revoked beyond single-token logout.
Can be replaced with a Redis-backed implementation without changing the interface.
"""
import uuid
from datetime import datetime, timezone


class SessionRecord:
    def __init__(
        self,
        jti: str,
        user_id: uuid.UUID,
        expires_at: datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ):
        self.jti = jti
        self.user_id = user_id
        self.created_at = datetime.now(timezone.utc)
        self.expires_at = expires_at
        self.ip_address = ip_address
        self.user_agent = user_agent
        self.is_active = True


class SessionSecurityService:

    def __init__(self):
        self._sessions: dict[str, SessionRecord] = {}

    def register(
        self,
        jti: str,
        user_id: uuid.UUID,
        expires_at: datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> SessionRecord:
        record = SessionRecord(jti, user_id, expires_at, ip_address, user_agent)
        self._sessions[jti] = record
        return record

    def revoke(self, jti: str) -> bool:
        record = self._sessions.get(jti)
        if record is None:
            return False
        record.is_active = False
        return True

    def is_active_session(self, jti: str) -> bool:
        """Returns True if the JTI is unknown (not revoked) or explicitly active."""
        record = self._sessions.get(jti)
        if record is None:
            return True
        return record.is_active

    def list_for_user(self, user_id: uuid.UUID) -> list[SessionRecord]:
        now = datetime.now(timezone.utc)
        return [
            s for s in self._sessions.values()
            if s.user_id == user_id and s.expires_at > now and s.is_active
        ]

    def list_all_active(self) -> list[SessionRecord]:
        now = datetime.now(timezone.utc)
        return [s for s in self._sessions.values() if s.expires_at > now and s.is_active]

    def clear(self) -> None:
        self._sessions.clear()


session_security = SessionSecurityService()
