"""API client for De'Longhi Comfort devices."""

import asyncio
from collections.abc import Callable
import json
import logging
import urllib.parse
from datetime import datetime
from typing import Any

import aiohttp

from .const import (
    API_KEY,
    CLIENT_ID,
    AUTHORIZATION_HEADER,
    APP_ID,
    APP_SECRET,
    BROWSER_USER_AGENT,
    TOKEN_USER_AGENT,
    API_USER_AGENT,
    SDK_BUILD,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


class DeLonghiAPI:
    """API client for De'Longhi Comfort devices."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        language: str = "en",
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_update_callback: Callable[[str, str | None], None] | None = None,
    ) -> None:
        """Initialize the API client."""
        self.username = username
        self.password = password
        self.language = language
        self.session = session
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._token_update_callback = token_update_callback
        self._auth_lock = asyncio.Lock()

    def _notify_token_update(self) -> None:
        """Notify listener when tokens are updated."""
        if self._token_update_callback and self.access_token:
            self._token_update_callback(self.access_token, self.refresh_token)

    def get_query_param(self, url: str, param: str) -> str | None:
        """Extract query parameter from URL."""
        query = urllib.parse.urlparse(url).query
        params = urllib.parse.parse_qs(query)
        return params.get(param, [None])[0]

    def url_encode(self, value: str) -> str:
        """URL encode a value."""
        return urllib.parse.quote(value)

    async def authenticate(self) -> bool:
        """Authenticate with the De'Longhi API."""
        async with self._auth_lock:
            if self.access_token:
                return True
            try:
                if self.refresh_token:
                    self.access_token = await self._refresh_access_token_unlocked()
                else:
                    self.access_token = await self._get_new_access_token()
                return self.access_token is not None
            except Exception as e:
                _LOGGER.error("Authentication failed: %s", e)
                return False

    async def _fetch_auth_context(self) -> str | None:
        """Step 1: Start OIDC authentication process and get context."""
        async with self.session.get(
            f"https://fidm.eu1.gigya.com/oidc/op/v1.0/{API_KEY}/authorize",
            headers={"User-Agent": BROWSER_USER_AGENT},
            params={
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": "https://google.it",
                "scope": "openid email profile UID comfort en alexa",
                "nonce": str(int(datetime.now().timestamp())),
            },
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            return self.get_query_param(response.headers["Location"], "context")

    async def _fetch_gigya_ids(self) -> dict[str, Any]:
        """Step 2: Fetch Gigya session IDs."""
        async with self.session.get(
            "https://socialize.eu1.gigya.com/socialize.getIDs",
            headers={"User-Agent": BROWSER_USER_AGENT},
            params={
                "APIKey": API_KEY,
                "includeTicket": "true",
                "pageURL": "https://aylaopenid.delonghigroup.com/",
                "sdk": "js_latest",
                "sdkBuild": SDK_BUILD,
                "format": "json",
            },
            timeout=REQUEST_TIMEOUT,
        ) as response:
            return await response.json(content_type=None)

    async def _get_new_access_token(self) -> str | None:
        """Get a new access token through the full authentication flow."""
        # Thanks to https://github.com/duckwc/ECAMpy for the code to token convertion
        # Thanks to https://github.com/rtfpessoa/delonghi-comfort-client, for the cli code to implement the authentication flow
        try:
            # Steps 1 & 2 are independent; run them in parallel
            context, data = await asyncio.gather(
                self._fetch_auth_context(),
                self._fetch_gigya_ids(),
            )

            ucid = data["ucid"]
            gmid = data["gmid"]
            gmid_ticket = data["gmidTicket"]

            # Step 3: Login
            risk_context_json = json.dumps(
                {
                    "b0": 4494,
                    "b1": [0, 2, 2, 0],
                    "b2": 2,
                    "b3": [],
                    "b4": 2,
                    "b5": 1,
                    "b6": BROWSER_USER_AGENT,
                    "b7": [
                        {
                            "name": "PDF Viewer",
                            "filename": "internal-pdf-viewer",
                            "length": 2,
                        },
                        {
                            "name": "Chrome PDF Viewer",
                            "filename": "internal-pdf-viewer",
                            "length": 2,
                        },
                        {
                            "name": "Chromium PDF Viewer",
                            "filename": "internal-pdf-viewer",
                            "length": 2,
                        },
                        {
                            "name": "Microsoft Edge PDF Viewer",
                            "filename": "internal-pdf-viewer",
                            "length": 2,
                        },
                        {
                            "name": "WebKit built-in PDF",
                            "filename": "internal-pdf-viewer",
                            "length": 2,
                        },
                    ],
                    "b8": datetime.now().strftime("%H:%M:%S"),
                    "b9": 0,
                    "b10": {"state": "denied"},
                    "b11": False,
                    "b13": [5, "440|956|24", False, True],
                }
            )

            async with self.session.post(
                "https://accounts.eu1.gigya.com/accounts.login",
                headers={"User-Agent": BROWSER_USER_AGENT},
                data={
                    "loginID": self.username,
                    "password": self.password,
                    "sessionExpiration": 7884009,
                    "targetEnv": "jssdk",
                    "include": "profile,data,emails,subscriptions,preferences",
                    "includeUserInfo": "true",
                    "loginMode": "standard",
                    "lang": self.language,
                    "riskContext": self.url_encode(risk_context_json),
                    "APIKey": API_KEY,
                    "source": "showScreenSet",
                    "sdk": "js_latest",
                    "authMode": "cookie",
                    "pageURL": "https://aylaopenid.delonghigroup.com/",
                    "gmid": gmid,
                    "ucid": ucid,
                    "sdkBuild": SDK_BUILD,
                    "format": "json",
                },
                timeout=REQUEST_TIMEOUT,
            ) as response:
                login_data = await response.json(content_type=None)

            if login_data.get("statusCode") != 200:
                _LOGGER.error(
                    "Login failed: %s", login_data.get("errorDetails", "Unknown error")
                )
                return None

            login_token = login_data["sessionInfo"]["login_token"]

            # Step 4: Get user info
            async with self.session.post(
                "https://socialize.eu1.gigya.com/socialize.getUserInfo",
                headers={"User-Agent": BROWSER_USER_AGENT},
                data={
                    "enabledProviders": "*",
                    "APIKey": API_KEY,
                    "sdk": "js_latest",
                    "login_token": login_token,
                    "authMode": "cookie",
                    "pageURL": "https://aylaopenid.delonghigroup.com/",
                    "gmid": gmid,
                    "ucid": ucid,
                    "sdkBuild": SDK_BUILD,
                    "format": "json",
                },
                timeout=REQUEST_TIMEOUT,
            ) as response:
                user_data = await response.json(content_type=None)

            user_uid = user_data["UID"]
            user_uid_signature = user_data["UIDSignature"]
            user_signature_timestamp = user_data["signatureTimestamp"]

            # Step 5: Consent
            async with self.session.get(
                "https://aylaopenid.delonghigroup.com/OIDCConsentPage.php",
                headers={"User-Agent": BROWSER_USER_AGENT},
                params={
                    "lang": self.language,
                    "context": context,
                    "clientID": CLIENT_ID,
                    "scope": "openid+email+profile+UID+comfort+en+alexa",
                    "UID": user_uid,
                    "UIDSignature": user_uid_signature,
                    "signatureTimestamp": user_signature_timestamp,
                },
                timeout=REQUEST_TIMEOUT,
            ) as response:
                consent_text = await response.text()

            signature = consent_text.split("const consentObj2Sig = '")[1].split("';")[0]

            # Step 6: Authorization
            async with self.session.get(
                f"https://fidm.eu1.gigya.com/oidc/op/v1.0/{API_KEY}/authorize/continue",
                headers={"User-Agent": BROWSER_USER_AGENT},
                params={
                    "context": context,
                    "login_token": login_token,
                    "consent": json.dumps(
                        {
                            "scope": "openid email profile UID comfort en alexa",
                            "clientID": CLIENT_ID,
                            "context": context,
                            "UID": user_uid,
                            "consent": True,
                        },
                        separators=(",", ":"),
                    ),
                    "sig": signature,
                    "gmidTicket": gmid_ticket,
                },
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                code = self.get_query_param(response.headers["Location"], "code")

            # Step 7: Get IDP access token
            async with self.session.post(
                f"https://fidm.eu1.gigya.com/oidc/op/v1.0/{API_KEY}/token",
                headers={
                    "User-Agent": TOKEN_USER_AGENT,
                    "Authorization": AUTHORIZATION_HEADER,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": "https://google.it",
                },
                timeout=REQUEST_TIMEOUT,
            ) as response:
                token_data = await response.json(content_type=None)

            idp_token = token_data["access_token"]

            # Step 8: Exchange IDP token for Ayla token
            async with self.session.post(
                "https://user-field-eu.aylanetworks.com/api/v1/token_sign_in",
                headers={"User-Agent": TOKEN_USER_AGENT},
                data={
                    "app_id": APP_ID,
                    "app_secret": APP_SECRET,
                    "token": idp_token,
                },
                timeout=REQUEST_TIMEOUT,
            ) as response:
                ayla_data = await response.json(content_type=None)

            self.refresh_token = ayla_data["refresh_token"]
            self.access_token = ayla_data["access_token"]
            self._notify_token_update()
            return self.access_token

        except Exception as e:
            _LOGGER.error("Failed to get access token: %s", e)
            return None

    async def refresh_access_token(self) -> str | None:
        """Refresh the access token using the refresh token."""
        async with self._auth_lock:
            return await self._refresh_access_token_unlocked()

    async def _refresh_access_token_unlocked(self) -> str | None:
        """Refresh the access token (caller must hold _auth_lock)."""
        if not self.refresh_token:
            return await self._get_new_access_token()

        try:
            async with self.session.post(
                "https://user-field-eu.aylanetworks.com/users/refresh_token.json",
                headers={
                    "User-Agent": TOKEN_USER_AGENT,
                    "Content-Type": "application/json",
                },
                json={"user": {"refresh_token": self.refresh_token}},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    new_access_token = data["access_token"]
                    new_refresh_token = data.get("refresh_token")

                    if new_refresh_token:
                        self.refresh_token = new_refresh_token

                    self.access_token = new_access_token
                    self._notify_token_update()
                    return new_access_token

                _LOGGER.warning("Failed to refresh token, falling back to login")
                return await self._get_new_access_token()

        except Exception as e:
            _LOGGER.error("Failed to refresh access token: %s", e)
            return await self._get_new_access_token()

    async def get_devices(self) -> list[dict[str, Any]]:
        """Get all devices associated with the account."""
        if not self.access_token:
            if not await self.authenticate():
                return []

        try:
            response = await self._get_request("apiv1/devices.json")
            return response if isinstance(response, list) else []
        except Exception as e:
            _LOGGER.error("Failed to get devices: %s", e)
            return []

    async def get_device_properties(self, dsn: str) -> list[dict[str, Any]] | None:
        """Get device properties by DSN."""
        if not self.access_token:
            if not await self.authenticate():
                return None

        try:
            response = await self._get_request(f"apiv1/dsns/{dsn}/properties.json")
            return response if isinstance(response, list) else None
        except Exception as e:
            _LOGGER.error("Failed to get device properties for %s: %s", dsn, e)
            return None

    def get_property_value(
        self, properties: list[dict], property_name: str
    ) -> Any | None:
        """Extract a property value from the properties list by property name."""
        for prop in properties:
            if "property" in prop and prop["property"]["name"] == property_name:
                return prop["property"]["value"]
        return None

    async def set_device_mode(self, dsn: str, mode: int) -> bool:
        """Set device mode (1=Cooling, 2=Dehumidification, 3=Fan)."""
        try:
            response = await self._post_request(
                f"apiv1/dsns/{dsn}/properties/set_device_mode/datapoints.json",
                {"datapoint": {"value": mode}},
            )
            return response is not None
        except Exception as e:
            _LOGGER.error("Failed to set device mode for %s: %s", dsn, e)
            return False

    async def set_temperature_setpoint(self, dsn: str, temperature: float) -> bool:
        """Set temperature setpoint (16-32°C)."""
        try:
            response = await self._post_request(
                f"apiv1/dsns/{dsn}/properties/set_temp_setpoint/datapoints.json",
                {"datapoint": {"value": temperature}},
            )
            return response is not None
        except Exception as e:
            _LOGGER.error("Failed to set temperature setpoint for %s: %s", dsn, e)
            return False

    async def set_status(self, dsn: str, status: int) -> bool:
        """Set device status (1=ON, 2=OFF)."""
        try:
            response = await self._post_request(
                f"apiv1/dsns/{dsn}/properties/set_status/datapoints.json",
                {"datapoint": {"value": status}},
            )
            return response is not None
        except Exception as e:
            _LOGGER.error("Failed to set status for %s: %s", dsn, e)
            return False

    async def set_silent_mode(self, dsn: str, status: int) -> bool:
        """Set silent mode (1=ON, 2=OFF)."""
        try:
            response = await self._post_request(
                f"apiv1/dsns/{dsn}/properties/set_silent_function/datapoints.json",
                {"datapoint": {"value": status}},
            )
            return response is not None
        except Exception as e:
            _LOGGER.error("Failed to set silent mode for %s: %s", dsn, e)
            return False

    async def set_humidity_setpoint(self, dsn: str, humidity: int) -> bool:
        """Set humidity setpoint (0-100%)."""
        try:
            response = await self._post_request(
                f"apiv1/dsns/{dsn}/properties/humidity_setpoint/datapoints.json",
                {"datapoint": {"value": humidity}},
            )
            return response is not None
        except Exception as e:
            _LOGGER.error("Failed to set humidity setpoint for %s: %s", dsn, e)
            return False

    async def set_fan_speed(self, dsn: str, fan_speed: int) -> bool:
        """Set fan speed (1=Low, 2=Mid, 3=High, 4=Auto)."""
        try:
            response = await self._post_request(
                f"apiv1/dsns/{dsn}/properties/set_int_fan_speed/datapoints.json",
                {"datapoint": {"value": str(fan_speed)}},
            )
            return response is not None
        except Exception as e:
            _LOGGER.error("Failed to set fan speed for %s: %s", dsn, e)
            return False

    async def _ensure_fresh_token(self, failed_token: str | None) -> str | None:
        """Refresh token only if it has not already been updated by another caller."""
        async with self._auth_lock:
            if self.access_token and self.access_token != failed_token:
                return self.access_token
            return await self._refresh_access_token_unlocked()

    async def _get_request(self, path: str) -> dict[str, Any] | None:
        """Make a GET request to the API."""
        url = f"https://ads-eu.aylanetworks.com/{path}"
        used_token = self.access_token
        headers = {
            "User-Agent": API_USER_AGENT,
            "Authorization": f"auth_token {used_token}",
            "Accept": "application/json",
        }

        try:
            async with self.session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 401:  # Unauthorized
                # Try to refresh token and retry
                new_token = await self._ensure_fresh_token(used_token)
                if new_token:
                    headers["Authorization"] = f"auth_token {new_token}"
                    async with self.session.get(
                        url, headers=headers, timeout=REQUEST_TIMEOUT
                    ) as response:
                        response.raise_for_status()
                        return await response.json()
            return None
        except Exception as e:
            _LOGGER.error("GET request failed for %s: %s", path, e)
            return None

    async def _post_request(
        self, path: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Make a POST request to the API."""
        url = f"https://ads-eu.aylanetworks.com/{path}"
        used_token = self.access_token
        headers = {
            "User-Agent": API_USER_AGENT,
            "Authorization": f"auth_token {used_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self.session.post(
                url, headers=headers, json=data, timeout=REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            if e.status == 401:  # Unauthorized
                # Try to refresh token and retry
                new_token = await self._ensure_fresh_token(used_token)
                if new_token:
                    headers["Authorization"] = f"auth_token {new_token}"
                    async with self.session.post(
                        url, headers=headers, json=data, timeout=REQUEST_TIMEOUT
                    ) as response:
                        response.raise_for_status()
                        return await response.json()
            return None
        except Exception as e:
            _LOGGER.error("POST request failed for %s: %s", path, e)
            return None

    async def set_device_status(self, dsn: str, status: int) -> bool:
        """Set device status (1=ON, 2=OFF)."""
        try:
            response = await self._post_request(
                f"apiv1/dsns/{dsn}/properties/set_device_status/datapoints.json",
                {"datapoint": {"value": str(status)}},
            )
        except aiohttp.ClientError as e:
            _LOGGER.error("Failed to set device status for %s: %s", dsn, e)
            return False
        else:
            return response is not None

    async def set_device_property(self, dsn: str, propName: str, value: Any) -> bool:
        """Set a device property by DSN and property name."""
        try:
            response = await self._post_request(
                f"apiv1/dsns/{dsn}/properties/{propName}/datapoints.json",
                {"datapoint": {"value": value}},
            )
            return response is not None
        except Exception as e:
            _LOGGER.error("Failed to set device property: %s, %s, %s: %s", dsn, propName, value, e)
            return False
