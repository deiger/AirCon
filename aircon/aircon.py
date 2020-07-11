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
from dataclasses import fields
import enum
import random
import string
from Crypto.Cipher import AES

from .error import Error
from .properties import AcProperties, FastColdHeat, FglProperties, FglBProperties, HumidifierProperties
from .store import Data

class BaseDevice:
  def __init__(self, data: Data):
    self.data = data
    self._next_command_id = 0

  def queue_command(self, name: str, value) -> None:
    if self.data.properties.get_read_only(name):
      raise Error('Cannot update read-only property "{}".'.format(name))
    data_type = self.data.properties.get_type(name)
    base_type = self.data.properties.get_base_type(name)
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
    property_updater = lambda: self.data.update_property(name, typed_value)
    self.data.commands_queue.put_nowait((command, property_updater))

    # Handle turning on FastColdHeat
    if name == 't_temp_heatcold' and typed_value is FastColdHeat.ON:
      self.queue_command('t_fan_speed', 'AUTO')
      self.queue_command('t_fan_mute', 'OFF')
      self.queue_command('t_sleep', 'STOP')
      self.queue_command('t_temp_eight', 'OFF')

  def queue_status(self) -> None:
    for data_field in fields(self.data.properties):
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
      self.data.commands_queue.put_nowait((command, None))

class AcDevice(BaseDevice):
  def __init__(self):
    data = Data(properties=AcProperties())
    super().__init__(data)

class FglDevice(BaseDevice):
  def __init__(self):
    data = Data(properties=FglProperties())
    super().__init__(data)

class FglBProperties(BaseDevice):
  def __init__(self):
    data = Data(properties=FglBProperties())
    super().__init__(data)

class HumidifierDevice(BaseDevice):
  def __init__(self):
    data = Data(properties=HumidifierDevice())
    super().__init__(data)
