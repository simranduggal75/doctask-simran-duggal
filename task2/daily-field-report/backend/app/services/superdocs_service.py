import httpx

from app.config import SUPERDOCS_API_KEY, SUPERDOCS_BASE_URL


class SuperDocsError(Exception):
    """Raised when a SuperDocs API request fails."""


class SuperDocsClient:
    def __init__(self) -> None:
        self.base_url = SUPERDOCS_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {SUPERDOCS_API_KEY}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        *,
        session_id: str,
        message: str,
        document_html: str | None = None,
    ) -> dict:
        """Send a normal synchronous chat/edit request."""

        payload = {
            "message": message,
            "session_id": session_id,
        }

        if document_html is not None:
            payload["document_html"] = document_html

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat",
                headers=self.headers,
                json=payload,
            )

        if response.is_error:
            raise SuperDocsError(
                f"SuperDocs request failed: "
                f"{response.status_code} {response.text}"
            )

        return response.json()

    async def start_approval_job(
        self,
        *,
        session_id: str,
        message: str,
        document_html: str,
    ) -> dict:
        """Start an asynchronous AI edit that requires human approval."""

        payload = {
            "message": message,
            "session_id": session_id,
            "document_html": document_html,
            "approval_mode": "ask_every_time",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/async",
                headers=self.headers,
                json=payload,
            )

        if response.is_error:
            raise SuperDocsError(
                f"SuperDocs async request failed: "
                f"{response.status_code} {response.text}"
            )

        return response.json()

    async def get_job(self, job_id: str) -> dict:
        """Get the current status and result of an asynchronous job."""

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/v1/jobs/{job_id}",
                headers=self.headers,
            )

        if response.is_error:
            raise SuperDocsError(
                f"SuperDocs job request failed: "
                f"{response.status_code} {response.text}"
            )

        return response.json()

    async def approve_changes(
        self,
        *,
        session_id: str,
        job_id: str,
        approved: bool,
        changes: list[dict] | None = None,
    ) -> dict:
        """Approve or deny proposed SuperDocs changes."""

        payload = {
            "job_id": job_id,
            "approved": approved,
        }

        if changes is not None:
            payload["changes"] = changes

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/{session_id}/approve",
                headers=self.headers,
                json=payload,
            )

        if response.is_error:
            raise SuperDocsError(
                f"SuperDocs approval request failed: "
                f"{response.status_code} {response.text}"
            )

        return response.json()