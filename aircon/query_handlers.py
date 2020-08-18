#!/usr/bin/env python3.7
"""
Query handlers for the air conditioner module server.
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


def pad(data: bytes):
  """Zero padding for AES data encryption (non standard)."""
  new_size = math.ceil(len(data) / AES.block_size) * AES.block_size
  return data.ljust(new_size, bytes([0]))


def unpad(data: bytes):
  """Remove Zero padding for AES data encryption (non standard)."""
  return data.rstrip(bytes([0]))


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
