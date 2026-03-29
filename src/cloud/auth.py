import logging
from typing import Optional

import httpx

AUTH_HOST = "https://webapp-prod.cloud.remarkable.engineering"

_logger = logging.getLogger(__name__)


class AuthManager:
    """
    Manages reMarkable authentication tokens for Cloudflare Workers.

    Tokens are sourced from Workers Secrets (environment variables) on
    first use, and refreshed user tokens are persisted to Cloudflare KV.

    Args:
        device_token: reMarkable device token (from RM_DEVICE_TOKEN secret).
        user_token: Initial reMarkable user token (from RM_USER_TOKEN secret).
        kv_namespace: Cloudflare KV namespace binding (RMJOURNAL_AUTH).
                      If None, token refresh will not be persisted.
    """

    def __init__(
        self,
        device_token: str,
        user_token: str,
        kv_namespace=None,
    ):
        self.device_token = device_token
        self.user_token = user_token
        self._kv = kv_namespace

    async def _load_token_from_kv(self) -> Optional[str]:
        """Load refreshed user token from KV if available."""
        if self._kv is None:
            return None
        try:
            value = await self._kv.get("auth:user_token")
            return value
        except Exception as e:
            _logger.warning(f"Failed to load user token from KV: {e}")
            return None

    async def _save_token_to_kv(self, token: str):
        """Persist refreshed user token to KV."""
        if self._kv is None:
            return
        try:
            await self._kv.put("auth:user_token", token)
        except Exception as e:
            _logger.warning(f"Failed to save user token to KV: {e}")

    async def refresh_user_token(self) -> str:
        """Fetch a new user token using the device token."""
        if not self.device_token:
            raise ValueError(
                "Device token is missing (RM_DEVICE_TOKEN secret not set)."
            )

        url = f"{AUTH_HOST}/token/json/2/user/new"
        headers = {"Authorization": f"Bearer {self.device_token}"}

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers)

        if response.status_code == 200:
            self.user_token = response.text.strip()
            await self._save_token_to_kv(self.user_token)
            return self.user_token
        else:
            raise Exception(
                f"Failed to refresh user token: {response.status_code} {response.text}"
            )

    async def get_user_token(self, force_refresh: bool = False) -> str:
        """
        Return a valid user token.

        On first call, attempts to load a refreshed token from KV.
        Falls back to the initial token provided at construction.
        """
        if force_refresh:
            return await self.refresh_user_token()

        # Try KV first (may have a more recent token than the secret)
        kv_token = await self._load_token_from_kv()
        if kv_token:
            self.user_token = kv_token

        if not self.user_token:
            return await self.refresh_user_token()

        return self.user_token
