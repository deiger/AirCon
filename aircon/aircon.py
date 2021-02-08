from copy import deepcopy
from dataclasses import dataclass, field, fields
import enum
import logging
import random
import re
import string
import threading
import time
from typing import Any, Callable, Dict, List
import queue
from Crypto.Cipher import AES

from . import control_value
from .config import Config, Encryption
from .error import Error
from .properties import (AcProperties, AirFlow, AirFlowState, Economy, FanSpeed, FastColdHeat,
                         FglProperties, FglBProperties, HumidifierProperties, Properties, Power,
                         AcWorkMode, Quiet, TemperatureUnit)


@dataclass(order=True)
class Command:
  priority: int
  timestamp: int  # Aligns equal priority commands in FIFO.
  command: Dict = field(compare=False)
  updater: Callable = field(compare=False)


class Device(object):

  _FGL_DEVICES = re.compile(r'AP-W[ACDF]\dE')
  _FGLB_DEVICES = re.compile(r'AP-WB\dE')
  _HUMI_DEVICES = re.compile(r'0001-0401-000[12]')

  def __init__(self, config: Dict[str, str], properties: Properties, notifier: Callable[[None],
                                                                                        None]):
    self.name = config['name']
    self.app = config['app']
    self.model = config['model']
    self.sw_version = config['sw_version']
    self.mac_address = config['mac_address']
    self.ip_address = config['ip_address']
    self.temp_type = (TemperatureUnit.CELSIUS
                      if config.get('temp_type') == 'C' else TemperatureUnit.FAHRENHEIT)
    self._config = Config(config['lanip_key'], config['lanip_key_id'])
    self._properties = properties
    self._properties_lock = threading.RLock()
    self._queue_listener = notifier
    self._available = False
    self.topics = {}
    self.work_modes = []
    self.fan_modes = []

    self._next_command_id = 0

    self.commands_queue = queue.PriorityQueue()
    self._commands_seq_no = 0
    self._commands_seq_no_lock = threading.Lock()

    self._updates_seq_no = 0
    self._updates_seq_no_lock = threading.Lock()

    self._property_change_listeners = []  # type List[Callable[[str, Any], None]]

  @classmethod
  def create(cls, config: Dict[str, str], notifier: Callable[[None], None]):
    model = config['model']
    if cls._FGL_DEVICES.fullmatch(model):
      return FglDevice(config, notifier)
    if cls._FGLB_DEVICES.fullmatch(model):
      return FglBDevice(config, notifier)
    if cls._HUMI_DEVICES.fullmatch(model):
      return HumidifierDevice(config, notifier)
    return AcDevice(config, notifier)

  @property
  def is_fahrenheit(self) -> bool:
    return self.temp_type == TemperatureUnit.FAHRENHEIT

  @property
  def available(self) -> bool:
    return self._available

  @available.setter
  def available(self, value: bool):
    self._available = value
    self._notify_listeners('available', 'online' if value else 'offline')

  def add_property_change_listener(self, listener: Callable[[str, Any], None]):
    self._property_change_listeners.append(listener)

  def remove_property_change_listener(self, listener: Callable[[str, Any], None]):
    self._property_change_listeners.remove(listener)

  def _notify_listeners(self, prop_name: str, value):
    for listener in self._property_change_listeners:
      listener(self.mac_address, prop_name, value)

  def get_all_properties(self) -> Properties:
    with self._properties_lock:
      return deepcopy(self._properties)

  def get_property(self, name: str):
    """Get a stored property (or None if doesn't exist)."""
    with self._properties_lock:
      return getattr(self._properties, name, None)

  def get_property_type(self, name: str):
    return self._properties.get_type(name)

  def update_property(self, name: str, value, notify_value=None) -> None:
    """Update the stored properties, if changed."""
    if notify_value is None:
      notify_value = value
    with self._properties_lock:
      old_value = getattr(self._properties, name)
      if value != old_value:
        setattr(self._properties, name, value)
        # logging.debug('Updated properties: %s' % self._properties)
        if name == 't_control_value':
          self._update_controlled_properties(value)
      self._notify_listeners(name, notify_value)

  def _update_controlled_properties(self, control: int):
    raise NotImplementedError()

  def get_command_seq_no(self) -> int:
    with self._commands_seq_no_lock:
      seq_no = self._commands_seq_no
      self._commands_seq_no += 1
      return seq_no

  def is_update_valid(self, cur_update_no: int) -> bool:
    with self._updates_seq_no_lock:
      # Every once in a while the sequence number is zeroed out, so accept it.
      if self._updates_seq_no > cur_update_no and cur_update_no > 0:
        logging.error('Stale update found %d. Last update used is %d.', cur_update_no,
                      self._updates_seq_no)
        return False  # Old update
      self._updates_seq_no = cur_update_no
      return True

  def queue_command(self, name: str, value) -> None:
    if self._properties.get_read_only(name):
      raise Error('Cannot update read-only property "{}".'.format(name))
    data_type = self._properties.get_type(name)

    # Device mode is set using t_control_value
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
    # Add as a high priority command.
    self.commands_queue.put_nowait(Command(10, time.time_ns(), command, property_updater))

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
      # Add as a lower-priority command.
      self.commands_queue.put_nowait(Command(100, time.time_ns(), command, None))
    self._queue_listener()

  def update_key(self, key: dict) -> dict:
    return self._config.update(key)

  def get_app_encryption(self) -> Encryption:
    return self._config.app

  def get_dev_encryption(self) -> Encryption:
    return self._config.dev


class AcDevice(Device):

  def __init__(self, config: Dict[str, str], notifier: Callable[[None], None]):
    super().__init__(config, AcProperties(), notifier)
    self.topics = {
        'env_temp': 'f_temp_in',
        'fan_speed': 't_fan_speed',
        'work_mode': 't_work_mode',
        'power': 't_power',
        'swing_mode': 't_fan_power',
        'temp': 't_temp'
    }
    self.work_modes = ['off', 'fan_only', 'heat', 'cool', 'dry', 'auto']
    self.fan_modes = ['auto', 'lower', 'low', 'medium', 'high', 'higher']

  # @override to add special support for t_power.
  def update_property(self, name: str, value) -> None:
    with self._properties_lock:
      # HomeAssistant expects an 'off' work mode when the AC is off.
      notify_value = 'off' if name == 't_work_mode' and self.get_power() == Power.OFF else None
      super().update_property(name, value, notify_value)
      # HomeAssistant doesn't listen to changes in t_power, so notify also on a t_work_mode change.
      if name == 't_power':
        work_mode = 'off' if value == Power.OFF else self.get_work_mode()
        self._notify_listeners('t_work_mode', work_mode)

  # @override to add special support for t_power.
  def queue_command(self, name: str, value) -> None:
    # HomeAssistant doesn't have a designated turn on button in climate.mqtt.
    # Furthermore, turn_on doesn't send the right command...
    if name == 't_work_mode':
      if value == 'OFF':
        # Pass the command to t_power instead of t_work_mode.
        name = 't_power'
      else:
        # Also turn on the AC (if it hasn't already).
        super().queue_command('t_power', 'ON')

    # Run base.
    super().queue_command(name, value)

    # Handle turning on FastColdHeat
    if name == 't_temp_heatcold' and value == 'ON':
      super().queue_command('t_fan_speed', 'AUTO')
      super().queue_command('t_fan_mute', 'OFF')
      super().queue_command('t_sleep', 'STOP')
      super().queue_command('t_temp_eight', 'OFF')

  def get_env_temp(self) -> int:
    return self.get_property('f_temp_in')

  def set_power(self, setting: Power) -> None:
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_power(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_power', setting)

  def get_power(self) -> Power:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_power(control)
    else:
      return self.get_property('t_power')

  def set_temperature(self, setting: int) -> None:
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_temp(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_temp', setting)

  def get_temperature(self) -> int:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_temp(control)
    else:
      return self.get_property('t_temp')

  def set_work_mode(self, setting: AcWorkMode) -> None:
    control = self.get_property('t_control_value')
    if (control):
      if control_value.get_power(control) == Power.OFF:
        control = control_value.set_power(control, Power.ON)
      control = control_value.set_work_mode(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_work_mode', setting)

  def get_work_mode(self) -> AcWorkMode:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_work_mode(control)
    else:
      return self.get_property('t_work_mode')

  def set_fan_speed(self, setting: FanSpeed) -> None:
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_fan_speed(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_fan_speed', setting)

  def get_fan_speed(self) -> FanSpeed:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_fan_speed(control)
    else:
      return self.get_property('t_fan_speed')

  def set_fan_vertical(self, setting: AirFlow) -> None:
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_fan_power(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_fan_power', setting)

  def get_fan_vertical(self) -> AirFlow:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_fan_power(control)
    else:
      return self.get_property('t_fan_power')

  def set_fan_horizontal(self, setting: AirFlow) -> None:
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_fan_lr(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_fan_leftright', setting)

  def get_fan_horizontal(self) -> AirFlow:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_fan_lr(control)
    else:
      return self.get_property('t_fan_leftright')

  def set_fan_mute(self, setting: Quiet) -> None:
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_fan_mute(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_fan_mute', setting)

  def get_fan_mute(self) -> Quiet:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_fan_mute(control)
    else:
      return self.get_property('t_fan_mute')

  def set_fast_heat_cold(self, setting: FastColdHeat):
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_heat_cold(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_temp_heatcold', setting)

  def get_fast_heat_cold(self) -> FastColdHeat:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_heat_cold(control)
    else:
      return self.get_property('t_temp_heatcold')

  def set_eco(self, setting: Economy) -> None:
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_eco(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_eco', setting)

  def get_eco(self) -> Economy:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_eco(control)
    else:
      return self.get_property('t_eco')

  def set_temptype(self, setting: TemperatureUnit) -> None:
    control = self.get_property('t_control_value')
    control = control_value.clear_up_change_flags(control)
    if (control):
      control = control_value.set_temptype(control, setting)
      self.queue_command('t_control_value', control)
    else:
      self.queue_command('t_temptype', setting)

  def get_temptype(self) -> TemperatureUnit:
    control = self.get_property('t_control_value')
    if (control):
      return control_value.get_temptype(control)
    else:
      return self.get_property('t_temptype')

  def set_swing(self, setting: AirFlowState) -> None:
    control = self.get_property("t_control_value")
    control = control_value.clear_up_change_flags(control)
    if control:
      if setting == AirFlowState.OFF:
        control = control_value.set_fan_power(control, AirFlow.OFF)
        control = control_value.set_fan_lr(control, AirFlow.OFF)
      elif setting == AirFlowState.VERTICAL_ONLY:
        control = control_value.set_fan_power(control, AirFlow.ON)
        control = control_value.set_fan_lr(control, AirFlow.OFF)
      elif setting == AirFlowState.HORIZONTAL_ONLY:
        control = control_value.set_fan_power(control, AirFlow.OFF)
        control = control_value.set_fan_lr(control, AirFlow.ON)
      elif setting == AirFlowState.VERTICAL_AND_HORIZONTAL:
        control = control_value.set_fan_power(control, AirFlow.ON)
        control = control_value.set_fan_lr(control, AirFlow.ON)
      self.queue_command("t_control_value", control)
    else:
      if setting == AirFlowState.OFF:
        self.queue_command("t_fan_speed", AirFlow.OFF)
        self.queue_command("t_fan_leftright", AirFlow.OFF)
      elif setting == AirFlowState.VERTICAL_ONLY:
        self.queue_command("t_fan_speed", AirFlow.ON)
        self.queue_command("t_fan_leftright", AirFlow.OFF)
      elif setting == AirFlowState.HORIZONTAL_ONLY:
        self.queue_command("t_fan_speed", AirFlow.OFF)
        self.queue_command("t_fan_leftright", AirFlow.ON)
      elif setting == AirFlowState.VERTICAL_AND_HORIZONTAL:
        self.queue_command("t_fan_speed", AirFlow.ON)
        self.queue_command("t_fan_leftright", AirFlow.ON)

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

  def _update_controlled_properties(self, control: int):
    power = control_value.get_power(control)
    self.update_property('t_power', power)

    fan_speed = control_value.get_fan_speed(control)
    self.update_property('t_fan_speed', fan_speed)

    work_mode = control_value.get_work_mode(control)
    self.update_property('t_work_mode', work_mode)

    temp_heatcold = control_value.get_heat_cold(control)
    self.update_property('t_temp_heatcold', temp_heatcold)

    eco = control_value.get_eco(control)
    self.update_property('t_eco', eco)

    temp = control_value.get_temp(control)
    self.update_property('t_temp', temp)

    fan_power = control_value.get_fan_power(control)
    self.update_property('t_fan_power', fan_power)

    fan_horizontal = control_value.get_fan_lr(control)
    self.update_property('t_fan_leftright', fan_horizontal)

    fan_mute = control_value.get_fan_mute(control)
    self.update_property('t_fan_mute', fan_mute)

    temptype = control_value.get_temptype(control)
    self.update_property('t_temptype', temptype)


class FglDevice(Device):

  def __init__(self, config: Dict[str, str], notifier: Callable[[None], None]):
    super().__init__(config, FglProperties(), notifier)
    self.topics = {
        'fan_speed': 'fan_speed',
        'work_mode': 'operation_mode',
        'swing_mode': 'af_vertical_swing',
        'temp': 'adjust_temperature'
    }
    self.work_modes = ['off', 'fan_only', 'heat', 'cool', 'dry', 'auto']
    self.fan_modes = ['auto', 'quiet', 'low', 'medium', 'high']


class FglBDevice(Device):

  def __init__(self, config: Dict[str, str], notifier: Callable[[None], None]):
    super().__init__(config, FglBProperties(), notifier)
    self.topics = {
        'fan_speed': 'fan_speed',
        'work_mode': 'operation_mode',
        'temp': 'adjust_temperature'
    }
    self.work_modes = ['off', 'fan_only', 'heat', 'cool', 'dry', 'auto']
    self.fan_modes = ['auto', 'quiet', 'low', 'medium', 'high']


class HumidifierDevice(Device):

  def __init__(self, config: Dict[str, str], notifier: Callable[[None], None]):
    super().__init__(config, HumidifierProperties(), notifier)
    self.topics = {'env_temp': 'temp', 'power': 'switch'}
