"""
Server for controlling HiSense Air Conditioner WiFi modules.
These modules are embedded for example in the Israel Tornado ACs.
This module is based on reverse engineering of the AC protocol,
and is not affiliated with HiSense, Tornado or any other relevant
company.

In order to run this server, you need to provide it with the a
config file, that likes like this:
{"lanip_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
 "lanip_key_id":8888,
 "random_1":"YYYYYYYYYYYYYYYY",
 "time_1":201111111111111,
 "random_2":"XXXXXXXXXXXXXXXX",
 "time_2":111111111111}

The random/time values are regenerated on key exchange when the
server first starts talking with the AC, so is the lanip_key_id.
The lanip_key, on the other hand, is generated only on the
HiSense server. In order to get that value, you'll need to run query_cli.py

The code here relies on Python 3.7
"""
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

from .error import Error
from .properties import AcProperties, FastColdHeat, FglProperties, FglBProperties, HumidifierProperties, Properties

class BaseDevice:
  def __init__(self, properties: Properties):
    self._properties = properties
    self._properties_lock = threading.Lock()

    self._next_command_id = 0

    self.commands_queue = queue.Queue()
    self._commands_seq_no = 0
    self._commands_seq_no_lock = threading.Lock()

    self._updates_seq_no = 0
    self._updates_seq_no_lock = threading.Lock()

    self.change_listener: Callable[[str, str], None]

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
      if self.change_listener:
        self.change_listener(name, value)

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
    base_type = self._properties.get_base_type(name)
    if issubclass(data_type, enum.Enum):
      data_value = data_type[value].value
    elif data_type is int and type(value) is str and '.' in value:
      # Round rather than fail if the input is a float.
      # This is commonly the case for temperatures converted by HA from Celsius.
      data_value = round(float(value))
    else:
      data_value = data_type(value)
    command = {
      'properties': [{
        'property': {
          'base_type': base_type,
          'name': name,
          'value': data_value,
          'id': ''.join(random.choices(string.ascii_letters + string.digits, k=8)),
        }
      }]
    }
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

class AcDevice(BaseDevice):
  def __init__(self):
    super().__init__(properties=AcProperties())

class FglDevice(BaseDevice):
  def __init__(self):
    super().__init__(properties=FglProperties())

class FglBDevice(BaseDevice):
  def __init__(self):
    super().__init__(properties=FglBProperties())

class HumidifierDevice(BaseDevice):
  def __init__(self):
    super().__init__(properties=HumidifierDevice())
