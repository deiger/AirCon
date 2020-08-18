#!/usr/bin/env python3.7
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
HiSense server. In order to get that value, you'll need to either
sniff the TLS-encrypted network traffic, or fetch and unencrypt
the string locally stored by the app cache (using a rooted device).

The code here relies on Python 3.7
If running in Raspberry Pi, install Python 3.7 manually.
Also install additional libraries:
pip3.7 install dataclasses_json paho-mqtt pycryptodome retry
"""

__author__ = 'droreiger@gmail.com (Dror Eiger)'

import argparse
import base64
from dataclasses import dataclass, field, fields
from dataclasses_json import dataclass_json
import enum
import hmac
from http.client import HTTPConnection, InvalidURL
from http.server import HTTPServer, BaseHTTPRequestHandler
from http import HTTPStatus
import json
import logging
import logging.handlers
import math
import paho.mqtt.client as mqtt
import queue
import random
from retry import retry
import socket
import string
import sys
import threading
import time
import typing
from urllib.parse import parse_qs, urlparse, ParseResult

from Crypto.Cipher import AES


@dataclass_json
@dataclass
class LanConfig:
  lanip_key: str
  lanip_key_id: int
  random_1: str
  time_1: int
  random_2: str
  time_2: int


@dataclass
class Encryption:
  sign_key: bytes
  crypto_key: bytes
  iv_seed: bytes
  cipher: AES
  
  def __init__(self, lanip_key: bytes, msg: bytes):
    self.sign_key = self._build_key(lanip_key, msg + b'0')
    self.crypto_key = self._build_key(lanip_key, msg + b'1')
    self.iv_seed = self._build_key(lanip_key, msg + b'2')[:AES.block_size]
    self.cipher = AES.new(self.crypto_key, AES.MODE_CBC, self.iv_seed)

  @classmethod
  def _build_key(cls, lanip_key: bytes, msg: bytes) -> bytes:
    return cls.hmac_digest(lanip_key, cls.hmac_digest(lanip_key, msg) + msg)
  
  @staticmethod
  def hmac_digest(key: bytes, msg: bytes) -> bytes:
    return hmac.digest(key, msg, 'sha256')


@dataclass
class Config:
  lan_config: LanConfig
  app: Encryption
  dev: Encryption
  
  def __init__(self):
    with open(_parsed_args.config, 'rb') as f:
      self.lan_config = LanConfig.from_json(f.read().decode('utf-8'))
    self._update_encryption()
    
  def update(self):
    """Updates the stored lan config, and encryption data."""
    with open(_parsed_args.config, 'wb') as f:
      f.write(self.lan_config.to_json().encode('utf-8'))
    self._update_encryption()

  def _update_encryption(self):
    lanip_key = self.lan_config.lanip_key.encode('utf-8')
    random_1 = self.lan_config.random_1.encode('utf-8')
    random_2 = self.lan_config.random_2.encode('utf-8')
    time_1 = str(self.lan_config.time_1).encode('utf-8')
    time_2 = str(self.lan_config.time_2).encode('utf-8')
    self.app = Encryption(lanip_key, random_1 + random_2 + time_1 + time_2)
    self.dev = Encryption(lanip_key, random_2 + random_1 + time_2 + time_1)

class Error(Exception):
  """Error class for AC handling."""
  pass

class AirFlow(enum.IntEnum):
  OFF = 0
  VERTICAL_ONLY = 1
  HORIZONTAL_ONLY = 2
  VERTICAL_AND_HORIZONTAL = 3

class FanSpeed(enum.IntEnum):
  AUTO = 0
  LOWER = 5
  LOW = 6
  MEDIUM = 7
  HIGH = 8
  HIGHER = 9

class SleepMode(enum.IntEnum):
  STOP = 0
  ONE = 1
  TWO = 2
  THREE = 3
  FOUR = 4

class StateMachine(enum.IntEnum):
  FANONLY = 0
  HEAT = 1
  COOL = 2
  DRY = 3
  AUTO = 4
  FAULTSHIELD = 5
  POWEROFF = 6
  OFFLINE = 7
  READONLYSHARED = 8

class AcWorkMode(enum.IntEnum):
  FAN = 0
  HEAT = 1
  COOL = 2
  DRY = 3
  AUTO = 4

class AirFlow(enum.Enum):
  OFF = 0
  ON = 1

class DeviceErrorStatus(enum.Enum):
  NORMALSTATE = 0
  FAULTSTATE = 1

class Dimmer(enum.Enum):
  ON = 0
  OFF = 1

class DoubleFrequency(enum.Enum):
  OFF = 0
  ON = 1

class Economy(enum.Enum):
  OFF = 0
  ON = 1

class EightHeat(enum.Enum):
  OFF = 0
  ON = 1

class FastColdHeat(enum.Enum):
  OFF = 0
  ON = 1

class Power(enum.Enum):
  OFF = 0
  ON = 1

class Quiet(enum.Enum):
  OFF = 0
  ON = 1

class TemperatureUnit(enum.Enum):
  CELSIUS = 0
  FAHRENHEIT = 1

class HumidifierWorkMode(enum.Enum):
  NORMAL = 0
  NIGHTLIGHT = 1
  SLEEP = 2

class HumidifierWater(enum.Enum):
  OK = 0
  NO_WATER = 1

class Mist(enum.Enum):
  SMALL = 1
  MIDDLE = 2
  BIG = 3

class MistState(enum.Enum):
  OFF = 0
  ON = 1

class FglOperationMode(enum.IntEnum):
  OFF = 0
  ON = 1
  AUTO = 2
  COOL = 3
  DRY = 4
  FAN = 5
  HEAT = 6

class FglFanSpeed(enum.IntEnum):
  QUIET = 0
  LOW = 1
  MEDIUM = 2
  HIGH = 3
  AUTO = 4



class Properties(object):
  @classmethod
  def _get_metadata(cls, attr: str):
    return cls.__dataclass_fields__[attr].metadata

  @classmethod
  def get_type(cls, attr: str):
    return cls.__dataclass_fields__[attr].type

  @classmethod
  def get_base_type(cls, attr: str):
    return cls._get_metadata(attr)['base_type']

  @classmethod
  def get_read_only(cls, attr: str):
    return cls._get_metadata(attr)['read_only']


@dataclass_json
@dataclass
class AcProperties(Properties):
  # ack_cmd: bool = field(default=None, metadata={'base_type': 'boolean', 'read_only': False})
  f_electricity: int = field(default=100, metadata={'base_type': 'integer', 'read_only': True})
  f_e_arkgrille: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_incoiltemp: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_incom: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_indisplay: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_ineeprom: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_inele: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_infanmotor: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_inhumidity: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_inkeys: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_inlow: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_intemp: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_invzero: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_outcoiltemp: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_outeeprom: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_outgastemp: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_outmachine2: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_outmachine: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_outtemp: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_outtemplow: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_e_push: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_filterclean: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_humidity: int = field(default=50, metadata={'base_type': 'integer', 'read_only': True})  # Humidity
  f_power_display: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': True})
  f_temp_in: float = field(default=81.0, metadata={'base_type': 'decimal', 'read_only': True})  # EnvironmentTemperature (Fahrenheit)
  f_voltage: int = field(default=0, metadata={'base_type': 'integer', 'read_only': True})
  t_backlight: Dimmer = field(default=Dimmer.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: Dimmer[x]}})  # DimmerStatus
  t_control_value: int = field(default=None, metadata={'base_type': 'integer', 'read_only': False})
  t_device_info: bool = field(default=0, metadata={'base_type': 'boolean', 'read_only': False})
  t_display_power: bool = field(default=None, metadata={'base_type': 'boolean', 'read_only': False})
  t_eco: Economy = field(default=Economy.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: Economy[x]}})
  t_fan_leftright: AirFlow = field(default=AirFlow.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: AirFlow[x]}})  # HorizontalAirFlow
  t_fan_mute: Quiet = field(default=Quiet.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: Quite[x]}})  # QuiteModeStatus
  t_fan_power: AirFlow = field(default=AirFlow.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: AirFlow[x]}})  # VerticalAirFlow
  t_fan_speed: FanSpeed = field(default=FanSpeed.AUTO, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: FanSpeed[x]}})  # FanSpeed
  t_ftkt_start: int = field(default=None, metadata={'base_type': 'integer', 'read_only': False})
  t_power: Power = field(default=Power.ON, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: Power[x]}})  # PowerStatus
  t_run_mode: DoubleFrequency = field(default=DoubleFrequency.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: DoubleFrequency[x]}})  # DoubleFrequency
  t_setmulti_value: int = field(default=None, metadata={'base_type': 'integer', 'read_only': False})
  t_sleep: SleepMode = field(default=SleepMode.STOP, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: SleepMode[x]}})  # SleepMode
  t_temp: int = field(default=81, metadata={'base_type': 'integer', 'read_only': False})  # CurrentTemperature
  t_temptype: TemperatureUnit = field(default=TemperatureUnit.FAHRENHEIT, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: TemperatureUnit[x]}})  # CurrentTemperatureUnit
  t_temp_eight: EightHeat = field(default=EightHeat.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: EightHeat[x]}})  # EightHeatStatus
  t_temp_heatcold: FastColdHeat = field(default=FastColdHeat.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: FastColdHeat[x]}})  # FastCoolHeatStatus
  t_work_mode: AcWorkMode = field(default=AcWorkMode.AUTO, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: AcWorkMode[x]}})  # WorkModeStatus


@dataclass_json
@dataclass
class HumidifierProperties(Properties):
  humi: int = field(default=0, metadata={'base_type': 'integer', 'read_only': False})
  mist: Mist = field(default=Mist.SMALL, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: Mist[x]}})
  mistSt: MistState = field(default=MistState.OFF, metadata={'base_type': 'integer', 'read_only': True,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: MistState[x]}})
  realhumi: int = field(default=0, metadata={'base_type': 'integer', 'read_only': True})
  remain: int = field(default=0, metadata={'base_type': 'integer', 'read_only': True})
  switch: Power = field(default=Power.ON, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: Power[x]}})
  temp: int = field(default=81, metadata={'base_type': 'integer', 'read_only': True})
  timer: int = field(default=-1, metadata={'base_type': 'integer', 'read_only': False})
  water: HumidifierWater = field(default=HumidifierWater.OK, metadata={'base_type': 'boolean', 'read_only': True,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: HumidifierWater[x]}})
  workmode: HumidifierWorkMode = field(default=HumidifierWorkMode.NORMAL, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: HumidifierWorkMode[x]}})


@dataclass_json
@dataclass
class FglProperties(Properties):
  operation_mode: FglOperationMode = field(default=FglOperationMode.AUTO, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: FglOperationMode[x]}})
  fan_speed: FglFanSpeed = field(default=FglFanSpeed.AUTO, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: FglFanSpeed[x]}})
  adjust_temperature: int = field(default=25, metadata={'base_type': 'integer', 'read_only': False})
  af_vertical_direction: int = field(default=3, metadata={'base_type': 'integer', 'read_only': False})
  af_vertical_swing: AirFlow = field(default=AirFlow.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: AirFlow[x]}})  # HorizontalAirFlow
  af_horizontal_direction: int = field(default=3, metadata={'base_type': 'integer', 'read_only': False})
  af_horizontal_swing: AirFlow = field(default=AirFlow.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: AirFlow[x]}})  # HorizontalAirFlow
  economy_mode: Economy = field(default=Economy.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: Economy[x]}})


@dataclass_json
@dataclass
class FglBProperties(Properties):
  operation_mode: FglOperationMode = field(default=FglOperationMode.AUTO, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: FglOperationMode[x]}})
  fan_speed: FglFanSpeed = field(default=FglFanSpeed.AUTO, metadata={'base_type': 'integer', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: FglFanSpeed[x]}})
  adjust_temperature: int = field(default=25, metadata={'base_type': 'integer', 'read_only': False})
  af_vertical_move_step1: int = field(default=3, metadata={'base_type': 'integer', 'read_only': False})
  af_horizontal_move_step1: int = field(default=3, metadata={'base_type': 'integer', 'read_only': False})
  economy_mode: Economy = field(default=Economy.OFF, metadata={'base_type': 'boolean', 'read_only': False,
    'dataclasses_json': {'encoder': lambda x: x.name, 'decoder': lambda x: Economy[x]}})


@dataclass
class Data:
  """The current data store: commands, updates and properties."""
  commands_queue = queue.Queue()
  commands_seq_no = 0
  commands_seq_no_lock = threading.Lock()
  updates_seq_no = 0
  updates_seq_no_lock = threading.Lock()
  properties: Properties
  properties_lock = threading.Lock()

  def get_property(self, name: str):
    """Get a stored property."""
    with self.properties_lock:
      return getattr(self.properties, name)

  def update_property(self, name: str, value) -> None:
    """Update the stored properties, if changed."""
    with self.properties_lock:
      old_value = getattr(self.properties, name)
      if value != old_value:
        setattr(self.properties, name, value)
        logging.debug('Updated properties: %s' % self.properties)
      mqtt_publish_update(name, value)


def queue_command(name: str, value, recursive: bool = False) -> None:
  if _data.properties.get_read_only(name):
    raise Error('Cannot update read-only property "{}".'.format(name))
  data_type = _data.properties.get_type(name)
  base_type = _data.properties.get_base_type(name)
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
  property_updater = lambda: _data.update_property(name, typed_value)
  _data.commands_queue.put_nowait((command, property_updater))

  # Handle turning on FastColdHeat
  if name == 't_temp_heatcold' and typed_value is FastColdHeat.ON:
    queue_command('t_fan_speed', 'AUTO', True)
    queue_command('t_fan_mute', 'OFF', True)
    queue_command('t_sleep', 'STOP', True)
    queue_command('t_temp_eight', 'OFF', True)
  if not recursive:
    with _keep_alive.run_lock:
      _keep_alive.run_lock.notify()


def pad(data: bytes):
  """Zero padding for AES data encryption (non standard)."""
  new_size = math.ceil(len(data) / AES.block_size) * AES.block_size
  return data.ljust(new_size, bytes([0]))


def unpad(data: bytes):
  """Remove Zero padding for AES data encryption (non standard)."""
  return data.rstrip(bytes([0]))


class KeepAliveThread(threading.Thread):
  """Thread to preiodically generate keep-alive requests."""
  
  _KEEP_ALIVE_INTERVAL = 10.0

  def __init__(self):
    self.run_lock = threading.Condition()
    self._alive = False
    sock = None
    try:
      sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
      sock.connect(('10.255.255.255', 1))
      local_ip = sock.getsockname()[0]
    finally:
      if sock:
        sock.close()
    self._headers = {
      'Accept': 'application/json',
      'Connection': 'Keep-Alive',
      'Content-Type': 'application/json',
      'Host': _parsed_args.ip,
      'Accept-Encoding': 'gzip'
    }
    self._json = {
      'local_reg': {
        'ip': local_ip,
        'notify': 0,
        'port': _parsed_args.port,
        'uri': "/local_lan"
      }
    }
    super(KeepAliveThread, self).__init__(name='Keep Alive thread')

  @retry(exceptions=ConnectionError, delay=0.5, max_delay=20, backoff=1.5, logger=logging)
  def _establish_connection(self, conn: HTTPConnection) -> None:
    method = 'PUT' if self._alive else 'POST'
    logging.debug('%s /local_reg.json %s', method, json.dumps(self._json))
    try:
      conn.request(method, '/local_reg.json', json.dumps(self._json), self._headers)
      resp = conn.getresponse()
      if resp.status != HTTPStatus.ACCEPTED:
        raise ConnectionError('Recieved invalid response for local_reg: ' + repr(resp))
      resp.read()
    except:
      self._alive = False
      raise
    finally:
      conn.close()
    self._alive = True

  def run(self) -> None:
    with self.run_lock:
      try:
        conn = HTTPConnection(_parsed_args.ip, timeout=5)
      except InvalidURL:
        logging.exception('Invalid IP provided.')
        _httpd.shutdown()
        return
      while True:
        try:
          self._establish_connection(conn)
        except:
          logging.exception('Failed to send local_reg keep alive to the AC.')
          _httpd.shutdown()
          return
        self._json['local_reg']['notify'] = int(
            _data.commands_queue.qsize() > 0 or self.run_lock.wait(self._KEEP_ALIVE_INTERVAL))


class QueryStatusThread(threading.Thread):
  """Thread to preiodically query the status of all properties.
  
  After start-up, essentailly all updates should be pushed to the server due
  to the keep alive, so this is just a belt and suspenders.
  """
  
  _STATUS_UPDATE_INTERVAL = 600.0
  _WAIT_FOR_EMPTY_QUEUE = 10.0

  def __init__(self):
    self._next_command_id = 0
    super(QueryStatusThread, self).__init__(name='Query Status thread')

  def run(self) -> None:
    while True:
      # In case the AC is stuck, and not fetching commands, avoid flooding
      # the queue with status updates.
      while _data.commands_queue.qsize() > 10:
        time.sleep(self._WAIT_FOR_EMPTY_QUEUE)
      for data_field in fields(_data.properties):
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
        _data.commands_queue.put_nowait((command, None))
      if _keep_alive:
        with _keep_alive.run_lock:
          _keep_alive.run_lock.notify()
      time.sleep(self._STATUS_UPDATE_INTERVAL)


class HTTPRequestHandler(BaseHTTPRequestHandler):
  """Handler for AC related HTTP requests."""

  def do_HEAD(self, code: HTTPStatus = HTTPStatus.OK) -> None:
    """Return a JSON header."""
    self.send_response(code)
    if code == HTTPStatus.OK:
      self.send_header('Content-type', 'application/json')
    self.end_headers()

  def do_GET(self) -> None:
    """Accepts get requests."""
    logging.debug('GET Request,\nPath: %s\n', self.path)
    parsed_url = urlparse(self.path)
    query = parse_qs(parsed_url.query)
    handler = self._HANDLERS_MAP.get(parsed_url.path)
    if handler:
      try:
        handler(self, parsed_url.path, query, {})
        return
      except:
        logging.exception('Failed to parse property.')
    self.do_HEAD(HTTPStatus.NOT_FOUND)

  def do_POST(self):
    """Accepts post requests."""
    content_length = int(self.headers['Content-Length'])
    post_data = self.rfile.read(content_length)
    logging.debug('POST request,\nPath: %s\nHeaders:\n%s\n\nBody:\n%s\n',
                  str(self.path), str(self.headers), post_data.decode('utf-8'))
    parsed_url = urlparse(self.path)
    query = parse_qs(parsed_url.query)
    data = json.loads(post_data)
    handler = self._HANDLERS_MAP.get(parsed_url.path)
    if handler:
      try:
        handler(self, parsed_url.path, query, data)
        return
      except:
        logging.exception('Failed to parse property.')
    self.do_HEAD(HTTPStatus.NOT_FOUND)

  def key_exchange_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles a key exchange.
    Accepts the AC's random and time and pass its own.
    Note that a key encryption component is the lanip_key, mapped to the
    lanip_key_id provided by the AC. This secret part is provided by HiSense
    server. Fortunately the lanip_key_id (and lanip_key) are static for a given
    AC.
    """
    try:
      key = data['key_exchange']
      if key['ver'] != 1 or key['proto'] != 1 or key.get('sec'):
        raise KeyError()
      _config.lan_config.random_1 = key['random_1']
      _config.lan_config.time_1 = key['time_1']
    except KeyError:
      logging.error('Invalid key exchange: %r', data)
      self.do_HEAD(HTTPStatus.BAD_REQUEST)
      return
    if key['key_id'] != _config.lan_config.lanip_key_id:
      logging.error('The key_id has been replaced!!\nOld ID was %d; new ID is %d.',
                    _config.lan_config.lanip_key_id, key['key_id'])
      self.do_HEAD(HTTPStatus.NOT_FOUND)
      return
    _config.lan_config.random_2 = ''.join(
        random.choices(string.ascii_letters + string.digits, k=16))
    _config.lan_config.time_2 = time.monotonic_ns() % 2**40
    _config.update()
    self.do_HEAD(HTTPStatus.OK)
    self._write_json({"random_2": _config.lan_config.random_2,
                      "time_2": _config.lan_config.time_2})

  def command_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles a command request.
    Request arrives from the AC. takes a command from the queue,
    builds the JSON, encrypts and signs it, and sends it to the AC.
    """
    command = {}
    with _data.commands_seq_no_lock:
      command['seq_no'] = _data.commands_seq_no
      _data.commands_seq_no += 1
    try:
      command['data'], property_updater = _data.commands_queue.get_nowait()
    except queue.Empty:
      command['data'], property_updater = {}, None
    self.do_HEAD(HTTPStatus.OK)
    self._write_json(self._encrypt_and_sign(command))
    if property_updater:
      property_updater()

  def property_update_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles a property update request.
    Decrypts, validates, and pushes the value into the local properties store.
    """
    try:
      update = self._decrypt_and_validate(data)
    except Error:
      logging.exception('Failed to parse property.')
      self.do_HEAD(HTTPStatus.BAD_REQUEST)
      return
    self.do_HEAD(HTTPStatus.OK)
    with _data.updates_seq_no_lock:
      # Every once in a while the sequence number is zeroed out, so accept it.
      if _data.updates_seq_no > update['seq_no'] and update['seq_no'] > 0:
        logging.error('Stale update found %d. Last update used is %d.',
                      (update['seq_no'], _data.updates_seq_no))
        return  # Old update
      _data.updates_seq_no = update['seq_no']
    try:
      if not update['data']:
        logging.debug('No value returned for seq_no %d, likely an unsupported property key.',
                      update['seq_no'])
        return
      name = update['data']['name']
      data_type = _data.properties.get_type(name)
      value = data_type(update['data']['value'])
      _data.update_property(name, value)
    except:
      logging.exception('Failed to handle %s', update)

  def get_status_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles get status request (by a smart home hub).
    Returns the current internally stored state of the AC.
    """
    with _data.properties_lock:
      data = _data.properties.to_dict()
    self.do_HEAD(HTTPStatus.OK)
    self._write_json(data)

  def queue_command_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles queue command request (by a smart home hub).
    """
    try:
      queue_command(query['property'][0], query['value'][0])
    except:
      logging.exception('Failed to queue command.')
      self.do_HEAD(HTTPStatus.BAD_REQUEST)
      return
    self.do_HEAD(HTTPStatus.OK)
    self._write_json({'queued commands': _data.commands_queue.qsize()})

  @staticmethod
  def _encrypt_and_sign(data: dict) -> dict:
    text = json.dumps(data).encode('utf-8')
    logging.debug('Encrypting: %s', text.decode('utf-8'))
    return {
      "enc": base64.b64encode(_config.app.cipher.encrypt(pad(text))).decode('utf-8'),
      "sign": base64.b64encode(Encryption.hmac_digest(_config.app.sign_key, text)).decode('utf-8')
    }

  @staticmethod
  def _decrypt_and_validate(data: dict) -> dict:
    text = unpad(_config.dev.cipher.decrypt(base64.b64decode(data['enc'])))
    sign = base64.b64encode(Encryption.hmac_digest(_config.dev.sign_key, text)).decode('utf-8')
    if sign != data['sign']:
      raise Error('Invalid signature for %s!' % text.decode('utf-8'))
    logging.info('Decrypted: %s', text.decode('utf-8'))
    return json.loads(text.decode('utf-8'))

  def _write_json(self, data: dict) -> None:
    """Send out the provided data dict as JSON."""
    logging.debug('Response:\n%s', json.dumps(data))
    self.wfile.write(json.dumps(data).encode('utf-8'))

  _HANDLERS_MAP = {
    '/hisense/status': get_status_handler,
    '/hisense/command': queue_command_handler,
    '/local_lan/key_exchange.json': key_exchange_handler,
    '/local_lan/commands.json': command_handler,
    '/local_lan/property/datapoint.json': property_update_handler,
    '/local_lan/property/datapoint/ack.json': property_update_handler,
    '/local_lan/node/property/datapoint.json': property_update_handler,
    '/local_lan/node/property/datapoint/ack.json': property_update_handler,
    # TODO: Handle these if needed.
    # '/local_lan/node/conn_status.json': connection_status_handler,
    # '/local_lan/connect_status': module_request_handler,
    # '/local_lan/status.json': setup_device_details_handler,
    # '/local_lan/wifi_scan.json': module_request_handler,
    # '/local_lan/wifi_scan_results.json': module_request_handler,
    # '/local_lan/wifi_status.json': module_request_handler,
    # '/local_lan/regtoken.json': module_request_handler,
    # '/local_lan/wifi_stop_ap.json': module_request_handler,
  }


def mqtt_on_connect(client: mqtt.Client, userdata, flags, rc):
  client.subscribe([(_mqtt_topics['sub'].format(data_field.name), 0)
                    for data_field in fields(_data.properties)])
  # Subscribe to subscription updates.
  client.subscribe('$SYS/broker/log/M/subscribe/#')


def mqtt_on_subscribe(payload: bytes):
  # The last segment in the space delimited string is the topic.
  topic = payload.decode('utf-8').rsplit(' ', 1)[-1]
  if topic not in _mqtt_topics['pub']:
    return
  name = topic.rsplit('/', 2)[1]
  mqtt_publish_update(name, _data.get_property(name))


def mqtt_on_message(client: mqtt.Client, userdata, message: mqtt.MQTTMessage):
  logging.info('MQTT message Topic: %r, Payload %r',
               message.topic, message.payload)
  if message.topic.startswith('$SYS/broker/log/M/subscribe'):
    return mqtt_on_subscribe(message.payload)
  name = message.topic.rsplit('/', 2)[1]
  payload = message.payload.decode('utf-8')
  if name == 't_work_mode' and payload == 'fan_only':
    payload = 'FAN'
  try:
    queue_command(name, payload.upper())
  except Exception:
    logging.exception('Failed to parse value %r for property %r',
                      payload.upper(), name)


def mqtt_publish_update(name: str, value) -> None:
  if _mqtt_client:
    if isinstance(value, enum.Enum):
      payload = 'fan_only' if value is AcWorkMode.FAN else value.name.lower()
    else:
      payload = str(value)
    _mqtt_client.publish(_mqtt_topics['pub'].format(name),
                         payload=payload.encode('utf-8'))


def ParseArguments() -> argparse.Namespace:
  """Parse command line arguments."""
  arg_parser = argparse.ArgumentParser(
      description='JSON server for HiSense air conditioners.',
      allow_abbrev=False)
  arg_parser.add_argument('-p', '--port', required=True, type=int,
                          help='Port for the server.')
  arg_parser.add_argument('--ip', required=True,
                          help='IP address for the AC.')
  arg_parser.add_argument('--config', required=True,
                          help='LAN Config file.')
  arg_parser.add_argument('--device_type', default='ac',
                          choices={'ac', 'fgl', 'fgl_b', 'humidifier'},
                          help='Device type (for systems other than Hisense A/C).')
  arg_parser.add_argument('--mqtt_host', default=None,
                          help='MQTT broker hostname or IP address.')
  arg_parser.add_argument('--mqtt_port', type=int, default=1883,
                          help='MQTT broker port.')
  arg_parser.add_argument('--mqtt_client_id', default=None,
                          help='MQTT client ID.')
  arg_parser.add_argument('--mqtt_user', default=None,
                          help='<user:password> for the MQTT channel.')
  arg_parser.add_argument('--mqtt_topic', default='hisense_ac',
                          help='MQTT topic.')
  arg_parser.add_argument('--log_level', default='WARNING',
                          choices={'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'},
                          help='Minimal log level.')
  return arg_parser.parse_args()


if __name__ == '__main__':
  _parsed_args = ParseArguments()  # type: argparse.Namespace

  if sys.platform == 'linux':
    logging_handler = logging.handlers.SysLogHandler(address='/dev/log')
  elif sys.platform == 'darwin':
    logging_handler = logging.handlers.SysLogHandler(address='/var/run/syslog')
  elif sys.platform.lower() in ['windows', 'win32']:
    logging_handler = logging.handlers.SysLogHandler()
  else:  # Unknown platform, revert to stderr
    logging_handler = logging.StreamHandler(sys.stderr)
  logging_handler.setFormatter(
      logging.Formatter(fmt='{levelname[0]}{asctime}.{msecs:03.0f}  '
                        '{filename}:{lineno}] {message}',
                         datefmt='%m%d %H:%M:%S', style='{'))
  logger = logging.getLogger()
  logger.setLevel(_parsed_args.log_level)
  logger.addHandler(logging_handler)

  _config = Config()
  if _parsed_args.device_type == 'ac':
    _data = Data(properties=AcProperties())
  elif _parsed_args.device_type == 'fgl':
    _data = Data(properties=FglProperties())
  elif _parsed_args.device_type == 'fgl_b':
    _data = Data(properties=FglBProperties())
  elif _parsed_args.device_type == 'humidifier':
    _data = Data(properties=HumidifierProperties())
  else:
    sys.exit(1)  # Should never get here.

  _mqtt_client = None  # type: typing.Optional[mqtt.Client]
  _mqtt_topics = {}  # type: typing.Dict[str, str]
  if _parsed_args.mqtt_host:
    _mqtt_topics['pub'] = '/'.join((_parsed_args.mqtt_topic, '{}', 'status'))
    _mqtt_topics['sub'] = '/'.join((_parsed_args.mqtt_topic, '{}', 'command'))
    _mqtt_client = mqtt.Client(client_id=_parsed_args.mqtt_client_id,
                               clean_session=True)
    _mqtt_client.on_connect = mqtt_on_connect
    _mqtt_client.on_message = mqtt_on_message
    if _parsed_args.mqtt_user:
      _mqtt_client.username_pw_set(*_parsed_args.mqtt_user.split(':',1))
    _mqtt_client.connect(_parsed_args.mqtt_host, _parsed_args.mqtt_port)
    _mqtt_client.loop_start()

  _keep_alive = None  # type: typing.Optional[KeepAliveThread]

  query_status = QueryStatusThread()
  query_status.start()

  _keep_alive = KeepAliveThread()
  _keep_alive.start()

  _httpd = HTTPServer(('', _parsed_args.port), HTTPRequestHandler)
  try:
    _httpd.serve_forever()
  except KeyboardInterrupt:
    pass
  _httpd.server_close()
