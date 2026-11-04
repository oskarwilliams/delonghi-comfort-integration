"""The De'Longhi Comfort integration."""

import asyncio
from datetime import timedelta
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LANGUAGE,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_LANGUAGE,
    SCAN_INTERVAL,
)
from .api import DeLonghiAPI
from .coordinator import DeLonghiCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up De'Longhi Comfort from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    language = entry.data.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)

    session = async_get_clientsession(hass)

    def _update_tokens(access_token: str, refresh_token: str | None) -> None:
        """Persist updated tokens to config entry data."""
        new_data = {
            **entry.data,
            CONF_ACCESS_TOKEN: access_token,
            CONF_REFRESH_TOKEN: refresh_token,
        }
        if new_data != entry.data:
            hass.config_entries.async_update_entry(entry, data=new_data)

    api = DeLonghiAPI(
        username,
        password,
        session,
        language,
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        token_update_callback=_update_tokens,
    )

    # Test authentication
    if not await api.authenticate():
        _LOGGER.error("Failed to authenticate with De'Longhi API")
        return False

    # Get devices
    devices = await api.get_devices()
    if not devices:
        _LOGGER.error("No devices found")
        return False

    # Create coordinators for each device
    coordinators = []
    heater_coordinators = []
    for device in devices:
        device_info = device["device"]
        dsn = device_info["dsn"]

        coordinator = DeLonghiCoordinator(hass, api, dsn, device_info, entry)
        if device_info["oem_model"] == "DL-heater":
            heater_coordinators.append(coordinator)
        else:
            coordinators.append(coordinator)

    await asyncio.gather(
        *(
            coordinator.async_config_entry_first_refresh()
            for coordinator in (*coordinators, *heater_coordinators)
        )
    )

    # Store API instance, devices, and coordinators in hass data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "devices": devices,
        "coordinators": coordinators,
        "heater_coordinators": heater_coordinators,
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the De'Longhi Comfort integration."""
    # This integration is only configurable via config flow
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options."""
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, SCAN_INTERVAL)
    new_interval = timedelta(seconds=scan_interval)
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not entry_data:
        return

    for coordinator in (
        *entry_data["coordinators"],
        *entry_data["heater_coordinators"],
    ):
        coordinator.update_interval = new_interval
