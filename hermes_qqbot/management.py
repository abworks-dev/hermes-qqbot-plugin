"""Explicit QQ menu/panel management; constructing this client never publishes.

Official contracts (each method's request body is passed through unchanged):
https://bot.q.qq.com/wiki/develop/api-v2/server-inter/menu-panel/
https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_menu.get.html
https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_menu.put.html
https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.get.html
https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.post.html
https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id.get.html
https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id.put.html
https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id.delete.html
https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels_panel_id_target.put.html

Inject the adapter's bound ``_api_request``. Authentication, HTTP errors and
transport exceptions remain owned by that callable and propagate to the caller.
No retries, automatic pagination, reconciliation or startup writes are performed.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, urlencode


APIRequest = Callable[..., Awaitable[dict[str, Any]]]


class QQBotManagementClient:
    """Thin async client accepting complete documented JSON request bodies.

    Payload validation belongs to QQ, avoiding accidental rejection of optional
    fields, switch/submenu items, empty configurations or future schema additions.
    """

    def __init__(self, api_request: APIRequest) -> None:
        self._api_request = api_request

    async def get_global_menu(self) -> dict[str, Any]:
        """Read the C2C global menu and version (GET /v2/menu)."""
        return await self._api_request("GET", "/v2/menu")

    async def update_global_menu(self, payload: dict[str, Any]) -> dict[str, Any]:
        """PUT a full body, e.g. {'menu': {'items': [...]}}; replaces menu."""
        return await self._api_request("PUT", "/v2/menu", payload)

    async def list_panels(
        self, scope: str, *, cursor: str | None = None, limit: int | None = None,
    ) -> dict[str, Any]:
        """Read one page; scope is c2c/group/channel/dm, limit defaults to 20.

        QQ allows at most 50 records per page; next_cursor/is_end are returned
        unchanged so the caller explicitly controls subsequent requests.
        """
        params: dict[str, str | int] = {"scope": scope}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        return await self._api_request("GET", f"/v2/panels?{urlencode(params)}")

    async def create_panel(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST full body: scope, panel, optional target_type and openid lists.

        Specific targets are supported only for c2c/group; channel/dm are global.
        Returns QQ's panel_id response without extracting or discarding fields.
        """
        return await self._api_request("POST", "/v2/panels", payload)

    async def get_panel(self, panel_id: str) -> dict[str, Any]:
        """Read panel details, including associated openids where applicable."""
        return await self._api_request("GET", self._panel_path(panel_id))

    async def update_panel(self, panel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """PUT {'panel': {...}}; replaces items/remark, not associated targets."""
        return await self._api_request("PUT", self._panel_path(panel_id), payload)

    async def delete_panel(self, panel_id: str) -> dict[str, Any]:
        """Explicitly delete a panel; returns QQ's empty success object."""
        return await self._api_request("DELETE", self._panel_path(panel_id))

    async def update_panel_targets(self, panel_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """PUT op=add/del and user_openids or group_openids (up to 20 per call).

        Only specific c2c/group panels support associations. This does not change
        the panel's scope or target_type.
        """
        return await self._api_request("PUT", f"{self._panel_path(panel_id)}/target", payload)

    @staticmethod
    def _panel_path(panel_id: str) -> str:
        return f"/v2/panels/{quote(panel_id, safe='')}"
