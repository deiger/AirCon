#!/usr/bin/env python3.7
"""
Air conditioner devices for the air conditioner module server.
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
