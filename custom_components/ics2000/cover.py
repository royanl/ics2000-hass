"""Platform for cover integration (zonneschermen)."""
from __future__ import annotations

import logging
import threading
import voluptuous as vol

from typing import Any
from ics2000.Core import Hub
from ics2000.Devices import Device
from enum import Enum

import homeassistant.helpers.config_validation as cv
from homeassistant.components.cover import PLATFORM_SCHEMA, CoverEntity, CoverDeviceClass
from homeassistant.const import CONF_PASSWORD, CONF_MAC, CONF_EMAIL, CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)


# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_MAC): cv.string,
    vol.Required(CONF_EMAIL): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Optional('tries'): cv.positive_int,
    vol.Optional('sleep'): cv.positive_int,
    vol.Optional(CONF_IP_ADDRESS): cv.matches_regex(r'[1-9][0-9]{0,2}(\.(0|[1-9][0-9]{0,2})){2}\.[1-9][0-9]{0,2}'),
    vol.Optional('aes'): cv.matches_regex(r'[a-zA-Z0-9]{32}'),
    vol.Optional('cover_devices'): vol.All(cv.ensure_list, [cv.string])  # Device IDs die covers zijn
})


def setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType | None = None
) -> None:
    """Set up the ICS2000 Cover platform."""
    hub = Hub(
        config[CONF_MAC],
        config[CONF_EMAIL],
        config[CONF_PASSWORD]
    )

    if not hub.connected:
        _LOGGER.error("Could not connect to ICS2000 hub")
        return

    # Debug: Print alle devices met hun IDs
    _LOGGER.info("=== ICS2000 COVER DEVICES FOUND ===")
    for device in hub.devices:
        _LOGGER.info(f"Device ID: {device.id}, Name: {device.name}, Type: {type(device).__name__}")
    _LOGGER.info("=== END COVER DEVICE LIST ===")

    # Alleen devices toevoegen die als covers zijn geconfigureerd
    cover_device_ids = config.get('cover_devices', [])
    cover_devices = [device for device in hub.devices if str(device.id) in cover_device_ids]
    
    entities = []
    for device in cover_devices:
        _LOGGER.info(f"Adding cover device {device.name}")
        entities.append(KlikAanKlikUitCover(
            device=device,
            tries=int(config.get('tries', 1)),
            sleep=int(config.get('sleep', 3))
        ))

    add_entities(entities)


class KlikAanKlikUitCoverAction(Enum):
    OPEN = 'open'
    CLOSE = 'close'
    STOP = 'stop'


class KlikAanKlikUitCoverThread(threading.Thread):

    def __init__(self, action: KlikAanKlikUitCoverAction, device_id, target, kwargs):
        super().__init__(
            name=f'cover{action.value}{device_id}',
            target=target,
            kwargs=kwargs
        )

    @staticmethod
    def has_running_threads(device_id) -> bool:
        running_threads = [thread.name for thread in threading.enumerate() if thread.name in [
            f'cover{KlikAanKlikUitCoverAction.OPEN.value}{device_id}',
            f'cover{KlikAanKlikUitCoverAction.CLOSE.value}{device_id}',
            f'cover{KlikAanKlikUitCoverAction.STOP.value}{device_id}'
        ]]
        if running_threads:
            _LOGGER.info(f'Running cover threads: {",".join(running_threads)}')
            return True
        return False


class KlikAanKlikUitCover(CoverEntity):
    """Representation of a KlikAanKlikUit cover (zonnescherm)"""

    def __init__(self, device: Device, tries: int, sleep: int) -> None:
        """Initialize a KlikAanKlikUitCover"""
        self.tries = tries
        self.sleep = sleep
        self._name = device.name
        self._id = device.id
        self._hub = device.hub
        self._is_closed = None
        self._is_opening = False
        self._is_closing = False
        self.unique_id = f'kaku-cover-{device.id}'
        self._attr_device_class = CoverDeviceClass.AWNING
        
        _LOGGER.info(f'Adding cover with name {self._name}')

    @property
    def name(self) -> str:
        """Return the display name of this cover."""
        return self._name

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        return self._is_closed

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return self._is_opening

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return self._is_closing

    @property
    def icon(self) -> str:
        """Return icon for the cover."""
        if self._is_opening:
            return "mdi:arrow-up-bold"
        elif self._is_closing:
            return "mdi:arrow-down-bold"
        else:
            return "mdi:window-shutter"

    def open_cover(self, **kwargs: Any) -> None:
        """Open the cover (omhoog)."""
        _LOGGER.info(f'Opening cover {self._name}')
        
        if KlikAanKlikUitCoverThread.has_running_threads(self._id):
            _LOGGER.info(f'Thread already running for cover {self._id}, ignoring request')
            return

        self._is_opening = True
        self._is_closing = False
        self.schedule_update_ha_state()
        
        KlikAanKlikUitCoverThread(
            action=KlikAanKlikUitCoverAction.OPEN,
            device_id=self._id,
            target=self._execute_cover_action,
            kwargs={
                'action': 'open'
            }
        ).start()

    def close_cover(self, **kwargs: Any) -> None:
        """Close the cover (omlaag)."""
        _LOGGER.info(f'Closing cover {self._name}')
        
        if KlikAanKlikUitCoverThread.has_running_threads(self._id):
            _LOGGER.info(f'Thread already running for cover {self._id}, ignoring request')
            return

        self._is_closing = True
        self._is_opening = False
        self.schedule_update_ha_state()
        
        KlikAanKlikUitCoverThread(
            action=KlikAanKlikUitCoverAction.CLOSE,
            device_id=self._id,
            target=self._execute_cover_action,
            kwargs={
                'action': 'close'
            }
        ).start()

    def stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover movement."""
        _LOGGER.info(f'Stopping cover {self._name}')
        
        # Voor KlikAanKlikUit: stop door tegenovergestelde signaal te sturen
        if self._is_opening:
            # Als het omhoog gaat, stuur omlaag signaal om te stoppen
            hub_function = self._hub.turn_off
        elif self._is_closing:
            # Als het omlaag gaat, stuur omhoog signaal om te stoppen
            hub_function = self._hub.turn_on
        else:
            # Als het al gestopt is, doe niets
            return
        
        KlikAanKlikUitCoverThread(
            action=KlikAanKlikUitCoverAction.STOP,
            device_id=self._id,
            target=self._execute_cover_action,
            kwargs={
                'action': 'stop',
                'hub_function': hub_function
            }
        ).start()

    def _execute_cover_action(self, action: str, hub_function=None):
        """Execute cover action."""
        try:
            _LOGGER.info(f'Executing cover action {action} for {self._name}')
            
            if action == 'open':
                # Zonnescherm omhoog
                self._hub.turn_on(entity=self._id)
                
            elif action == 'close':
                # Zonnescherm omlaag
                self._hub.turn_off(entity=self._id)
                
            elif action == 'stop' and hub_function:
                # Stop door tegenovergestelde signaal
                hub_function(entity=self._id)
                
        except Exception as e:
            _LOGGER.error(f'Error executing cover action {action} for {self._name}: {e}')
        finally:
            # Reset states na actie
            self._is_opening = False
            self._is_closing = False
            
            # Na stop weten we niet meer wat de positie is
            if action == 'stop':
                self._is_closed = None
            elif action == 'open':
                self._is_closed = False
            elif action == 'close':
                self._is_closed = True
                
            self.schedule_update_ha_state()
            _LOGGER.info(f'Cover action {action} completed for {self._name}')

    def update(self) -> None:
        """Update cover state."""
        pass
