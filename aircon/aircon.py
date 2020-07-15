from copy import deepcopy
from dataclasses import fields
import enum
import logging
import random
import string
import threading
from typing import Callable
import queue
from Crypto.Cipher import AES

from .config import Config, Encryption
from .control_value_utils import (get_power_value, set_power_value, get_temp_value,
    set_temp_value, get_work_mode_value, set_work_mode_value, get_fan_speed_value,
    set_fan_speed_value, get_heat_cold_value, set_heat_cold_value, get_eco_value,
    set_eco_value, get_fan_power_value, set_fan_power_value, get_fan_lr_value,
    set_fan_lr_value, get_fan_mute_value, set_fan_mute_value, get_temptype_value,
    set_temptype_value)
from .error import Error
from .properties import (AcProperties, AirFlow, Economy, FanSpeed, FastColdHeat, FglProperties, FglBProperties, 
    HumidifierProperties, Properties, Power, AcWorkMode, Quiet, TemperatureUnit)

class BaseDevice:
  def __init__(self, name: str, ip_address: str, lanip_key: str, lanip_key_id: str, 
              properties: Properties, notifier: Callable[[None], None]):
    self.name = name
    self.ip_address = ip_address
    self._config = Config(lanip_key, lanip_key_id)
    self._properties = properties
    self._properties_lock = threading.Lock()
    self._queue_listener = notifier

    self._next_command_id = 0

    self.commands_queue = queue.Queue()
    self._commands_seq_no = 0
    self._commands_seq_no_lock = threading.Lock()

    self._updates_seq_no = 0
    self._updates_seq_no_lock = threading.Lock()

    self.property_change_listener: Callable[[str, str], None] = None

  def get_all_properties(self) -> Properties:
    with self._properties_lock:
      return deepcopy(self._properties)

  def get_property(self, name: str):
    """Get a stored property."""
    with self._properties_lock:
      return getattr(self._properties, name)

  def get_property_type(self, name: str):
    return self._properties.get_type(name)

  def update_property(self, name: str, value) -> None:
    """Update the stored properties, if changed."""
    with self._properties_lock:
      old_value = getattr(self._properties, name)
      if value != old_value:
        setattr(self._properties, name, value)
        logging.debug('Updated properties: %s' % self._properties)
      if self.property_change_listener:
        self.property_change_listener(self.name, name, value)

  def get_command_seq_no(self) -> int:
    with self._commands_seq_no_lock:
      seq_no = self._commands_seq_no
      self._commands_seq_no += 1
      return seq_no

  def is_update_valid(self, cur_update_no: int) -> bool:
    with self._updates_seq_no_lock:
      # Every once in a while the sequence number is zeroed out, so accept it.
      if self._updates_seq_no > cur_update_no and cur_update_no > 0:
        logging.error('Stale update found %d. Last update used is %d.',
                      cur_update_no, self._updates_seq_no)
        return False # Old update
      self._updates_seq_no = cur_update_no
      return True

  def queue_command(self, name: str, value) -> None:
    if self._properties.get_read_only(name):
      raise Error('Cannot update read-only property "{}".'.format(name))
    data_type = self._properties.get_type(name)

    # Device mode is set using control_value
    if issubclass(data_type, enum.Enum):
      data_value = data_type[value]
    elif data_type is int and type(value) is str and '.' in value:
      # Round rather than fail if the input is a float.
      # This is commonly the case for temperatures converted by HA from Celsius.
      data_value = round(float(value))
    else:
      data_value = data_type(value)
    
    # If device has set t_control_value it is being controlled by this field.
    if name != 't_control_value' and self.get_property('t_control_value'):
      self._convert_to_control_value(name, data_value)
      return
    
    if issubclass(data_type, enum.Enum):
      data_value = data_value.value

    command = self._build_command(name, data_value)
    # There are (usually) no acks on commands, so also queue an update to the
    # property, to be run once the command is sent.
    typed_value = data_type[value] if issubclass(data_type, enum.Enum) else data_value
    property_updater = lambda: self.update_property(name, typed_value)
    self.commands_queue.put_nowait((command, property_updater))

    # Handle turning on FastColdHeat
    if name == 't_temp_heatcold' and typed_value is FastColdHeat.ON:
      self.queue_command('t_fan_speed', 'AUTO')
      self.queue_command('t_fan_mute', 'OFF')
      self.queue_command('t_sleep', 'STOP')
      self.queue_command('t_temp_eight', 'OFF')
    
    self._queue_listener()

  def _build_command(self, name: str, data_value: int):
    base_type = self._properties.get_base_type(name)
    return {
      'properties': [{
        'property': {
          'base_type': base_type,
          'name': name,
          'value': data_value,
          'id': ''.join(random.choices(string.ascii_letters + string.digits, k=8)),
        }
      }]
    }

  def _convert_to_control_value(self, name: str, value) -> int:
    raise NotImplementedError()

  def queue_status(self) -> None:
    for data_field in fields(self._properties):
      command = {
        'cmds': [{
          'cmd': {
            'method': 'GET',
            'resource': 'property.json?name=' + data_field.name,
            'uri': '/local_lan/property/datapoint.json',
            'data': '',
            'cmd_id': self._next_command_id,
          }
        }]
      }
      self._next_command_id += 1
      self.commands_queue.put_nowait((command, None))
    self._queue_listener()

  def update_key(self, key: dict) -> dict:
    return self._config.update(key)

  def get_app_encryption(self) -> Encryption:
    return self._config.app

  def get_dev_encryption(self) -> Encryption:
    return self._config.dev

class AcDevice(BaseDevice):
  def __init__(self, name: str, ip_address: str, lanip_key: str, lanip_key_id: str,
              notifier: Callable[[None], None]):
    super().__init__(name, ip_address, lanip_key, lanip_key_id, AcProperties(), notifier)

  def get_env_temp(self) -> int:
    return self.get_property('f_temp_in')

  def set_power(self, setting: Power) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_power_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_power', setting)

  def get_power(self) -> Power:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_power_value(control_value)
    else:
      return self.get_property('t_power')

  def set_temperature(self, setting: int) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_temp_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_temp', setting)

  def get_temperature(self) -> int:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_temp_value(control_value)
    else:
      return self.get_property('t_temp')
    
  def set_work_mode(self, setting: AcWorkMode) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_work_mode_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_work_mode', setting)

  def get_work_mode(self) -> AcWorkMode:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_work_mode_value(control_value)
    else:
      return self.get_property('t_work_mode')

  def set_fan_speed(self, setting: FanSpeed) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_fan_speed_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_fan_speed', setting)

  def get_fan_speed(self) -> FanSpeed:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_fan_speed_value(control_value)
    else:
      return self.get_property('t_fan_speed')

  def set_fan_vertical(self, setting: AirFlow) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_fan_power_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_fan_power', setting)

  def get_fan_vertical(self) -> AirFlow:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_fan_power_value(control_value)
    else:
      return self.get_property('t_fan_power')

  def set_fan_horizontal(self, setting: AirFlow) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_fan_lr_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_fan_leftright', setting)

  def get_fan_horizontal(self) -> AirFlow:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_fan_lr_value(control_value)
    else:
      return self.get_property('t_fan_leftright')

  def set_fan_mute(self, setting: Quiet) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_fan_mute_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_fan_mute', setting)

  def get_fan_mute(self) -> Quiet:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_fan_mute_value(control_value)
    else:
      return self.get_property('t_fan_mute')

  def set_fast_heat_cold(self, setting: FastColdHeat):
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_heat_cold_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_temp_heatcold', setting)

  def get_fast_heat_cold(self) -> FastColdHeat:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_heat_cold_value(control_value)
    else:
      return self.get_property('t_temp_heatcold')

  def set_eco(self, setting: Economy) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_eco_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_eco', setting)
    
  def get_eco(self) -> Economy:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_eco_value(control_value)
    else:
      return self.get_property('t_eco')

  def set_temptype(self, setting: TemperatureUnit) -> None:
    control_value = self.get_property('t_control_value')
    if (control_value):
      control_value = set_temptype_value(control_value, setting)
      self.queue_command('t_control_value', control_value)
    else:
      self.queue_command('t_temptype', setting)

  def get_temptype(self) -> TemperatureUnit:
    control_value = self.get_property('t_control_value')
    if (control_value):
      return get_temptype_value(control_value)
    else:
      return self.get_property('t_temptype')

  def _convert_to_control_value(self, name: str, value) -> int:
    if name == 't_power':
      return self.set_power(value)
    elif name == 't_fan_speed':
      return self.set_fan_speed(value)
    elif name == 't_work_mode':
      return self.set_work_mode(value)
    elif name == 't_temp_heatcold':
      return self.set_fast_heat_cold(value)
    elif name == 't_eco':
      return self.set_eco(value)
    elif name == 't_temp':
      return self.set_temperature(value)
    elif name == 't_fan_power':
      return self.set_fan_vertical(value)
    elif name == 't_fan_leftright':
      return self.set_fan_horizontal(value)
    elif name == 't_fan_mute':
      return self.set_fan_mute(value)
    elif name == 't_temptype':
      return self.set_temptype(value)
    else:
      logging.error('Cannot convert to control value property {}'.format(name))
      raise ValueError()

class FglDevice(BaseDevice):
  def __init__(self, name: str, ip_address: str, lanip_key: str, 
              lanip_key_id: str, notifier: Callable[[None], None]):
    super().__init__(name, ip_address, lanip_key, lanip_key_id, FglProperties(), notifier)

class FglBDevice(BaseDevice):
  def __init__(self, name: str, ip_address: str, lanip_key: str, 
              lanip_key_id: str, notifier: Callable[[None], None]):
    super().__init__(name, ip_address, lanip_key, lanip_key_id, FglBProperties(), notifier)

class HumidifierDevice(BaseDevice):
  def __init__(self, name: str, ip_address: str, lanip_key: str,
              lanip_key_id: str, notifier: Callable[[None], None]):
    super().__init__(name, ip_address, lanip_key, lanip_key_id, HumidifierProperties(), notifier)
