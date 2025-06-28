"""Platform for switch integration (zonneschermen)."""
from __future__ import annotations

import logging
import time
import threading
import voluptuous as vol

from typing import Any
from ics2000.Core import Hub
from ics2000.Devices import Device
from enum import Enum

import homeassistant.helpers.config_validation as cv
from homeassistant.components.switch import PLATFORM_SCHEMA, SwitchEntity
from homeassistant.const import CONF_PASSWORD, CONF_MAC, CONF_EMAIL, CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)


def repeat(tries: int, sleep: int, callable_function, **kwargs):
    _LOGGER.info(f'Function repeat called in thread {threading.current_thread().name}')
    qualname = getattr(callable_function, '__qualname__')
    for i in range(0, tries):
        _LOGGER.info(f'Try {i + 1} of {tries} on {qualname}')
        callable_function(**kwargs)
        time.sleep(sleep if i != tries - 1 else 0)


# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_MAC): cv.string,
    vol.Required(CONF_EMAIL): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Optional('tries'): cv.positive_int,
    vol.Optional('sleep'): cv.positive_int,
    vol.Optional(CONF_IP_ADDRESS): cv.matches_regex(r'[1-9][0-9]{0,2}(\.(0|[1-9][0-9]{0,2})){2}\.[1-9][0-9]{0,2}'),
    vol.Optional('aes'): cv.matches_regex(r'[a-zA-Z0-9]{32}'),
    vol.Optional('awning_devices'): vol.All(cv.ensure_list, [cv.string])  # Device IDs die zonneschermen zijn
})


def setup_platform(
        hass: HomeAssistant,
        config: ConfigType,
        add_entities: AddEntitiesCallback,
        discovery_info: DiscoveryInfoType | None = None
) -> None:
    """Set up the ICS2000 Switch platform."""
    hub = Hub(
        config[CONF_MAC],
        config[CONF_EMAIL],
        config[CONF_PASSWORD]
    )

    if not hub.connected:
        _LOGGER.error("Could not connect to ICS2000 hub")
        return

    # Debug: Print alle devices met hun IDs
    _LOGGER.info("=== ICS2000 SWITCH DEVICES FOUND ===")
    for device in hub.devices:
        _LOGGER.info(f"Device ID: {device.id}, Name: {device.name}, Type: {type(device).__name__}")
    _LOGGER.info("=== END SWITCH DEVICE LIST ===")

    entities = []
    awning_device_ids = config.get('awning_devices', [])
    
    for device in hub.devices:
        if str(device.id) in awning_device_ids:
            # Voeg zonnescherm toe als twee aparte momentary switches
            _LOGGER.info(f"Adding awning device {device.name} as two momentary switches")
            entities.append(KlikAanKlikUitAwningSwitch(
                device=device,
                tries=int(config.get('tries', 1)),
                sleep=int(config.get('sleep', 3)),
                direction='up'
            ))
            entities.append(KlikAanKlikUitAwningSwitch(
                device=device,
                tries=int(config.get('tries', 1)),
                sleep=int(config.get('sleep', 3)),
                direction='down'
            ))

    add_entities(entities)


class KlikAanKlikUitAwningAction(Enum):
    UP = 'up'
    DOWN = 'down'


class KlikAanKlikUitAwningThread(threading.Thread):

    def __init__(self, action: KlikAanKlikUitAwningAction, device_id, target, kwargs):
        super().__init__(
            name=f'awning{action.value}{device_id}',
            target=target,
            kwargs=kwargs
        )

    @staticmethod
    def has_running_threads(device_id) -> bool:
        running_threads = [thread.name for thread in threading.enumerate() if thread.name in [
            f'awning{KlikAanKlikUitAwningAction.UP.value}{device_id}',
            f'awning{KlikAanKlikUitAwningAction.DOWN.value}{device_id}'
        ]]
        if running_threads:
            _LOGGER.info(f'Running awning threads: {",".join(running_threads)}')
            return True
        return False


class KlikAanKlikUitAwningSwitch(SwitchEntity):
    """Representation of a KlikAanKlikUit awning switch (momentary)"""

    def __init__(self, device: Device, tries: int, sleep: int, direction: str) -> None:
        """Initialize a KlikAanKlikUitAwningSwitch"""
        self.tries = tries
        self.sleep = sleep
        self.direction = direction
        self._name = f"{device.name} {direction.title()}"
        self._id = device.id
        self._hub = device.hub
        self._is_on = False
        self.unique_id = f'kaku-awning-{device.id}-{direction}'
        
        # Icons voor duidelijkheid
        if direction == 'up':
            self._attr_icon = 'mdi:arrow-up-bold'
        else:
            self._attr_icon = 'mdi:arrow-down-bold'
            
        _LOGGER.info(f'Adding awning switch with name {self._name}')

    @property
    def name(self) -> str:
        """Return the display name of this switch."""
        return self._name

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._is_on

    @property
    def icon(self) -> str:
        """Return the icon for this switch."""
        return self._attr_icon

    def turn_on(self, **kwargs: Any) -> None:
        """Activate the awning movement."""
        _LOGGER.info(f'Activating awning {self._name} in thread {threading.current_thread().name}')
        
        # Check if er al een thread actief is voor dit device
        if KlikAanKlikUitAwningThread.has_running_threads(self._id):
            _LOGGER.info(f'Thread already running for device {self._id}, ignoring request')
            return
        
        # Bepaal welke hub functie te gebruiken
        if self.direction == 'up':
            hub_function = self._hub.turn_on
            action = KlikAanKlikUitAwningAction.UP
        else:
            hub_function = self._hub.turn_off
            action = KlikAanKlikUitAwningAction.DOWN
        
        # Start beweging in aparte thread
        KlikAanKlikUitAwningThread(
            action=action,
            device_id=self._id,
            target=self._execute_movement,
            kwargs={
                'hub_function': hub_function
            }
        ).start()

    def turn_off(self, **kwargs: Any) -> None:
        """Switch automatically turns off (momentary behavior)."""
        _LOGGER.info(f'Deactivating awning {self._name}')
        self._is_on = False
        self.schedule_update_ha_state()

    def _execute_movement(self, hub_function):
        """Execute the movement and auto-turn off."""
        try:
            _LOGGER.info(f'Executing movement for {self._name}')
            
            # Schakel aan voor visuele feedback
            self._is_on = True
            self.schedule_update_ha_state()
            
            # Voer beweging uit met repeat functie
            repeat(
                tries=self.tries,
                sleep=self.sleep,
                callable_function=hub_function,
                entity=self._id
            )
            
            # Wacht kort en schakel automatisch uit (momentary gedrag)
            time.sleep(0.5)
            
        except Exception as e:
            _LOGGER.error(f'Error executing awning movement for {self._name}: {e}')
        finally:
            # Altijd uitschakelen na actie
            self._is_on = False
            self.schedule_update_ha_state()
            _LOGGER.info(f'Movement completed for {self._name}')

    def update(self) -> None:
        """Update switch state."""
        # Voor momentary switches hoeven we geen state updates te doen
        pass
