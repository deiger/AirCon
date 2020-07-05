import argparse
import base64
from http import HTTPStatus
from http.client import HTTPConnection, InvalidURL
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import logging
import paho.mqtt.client as mqtt
from retry import retry
import socket
import sys
import threading
import time
import _thread
from urllib.parse import parse_qs, urlparse, ParseResult

from . import aircon
from .config import Config
from .error import Error
from .aircon import DeviceController
from .properties import AcProperties, FglProperties, FglBProperties, HumidifierProperties
from .store import Data
from .mqtt_client import MqttClient
from .query_handlers import QueryHandlers

class KeepAliveThread(threading.Thread):
  """Thread to preiodically generate keep-alive requests."""
  
  _KEEP_ALIVE_INTERVAL = 10.0

  def __init__(self, host: str, port: int, data: Data):
    self.run_lock = threading.Condition()
    self._alive = False
    self._host = host
    self._port = port
    self._data = data
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
      'Host': self._host,
      'Accept-Encoding': 'gzip'
    }
    self._json = {
      'local_reg': {
        'ip': local_ip,
        'notify': 0,
        'port': self._port,
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
        conn = HTTPConnection(self._host, timeout=5)
      except InvalidURL:
        logging.exception('Invalid IP provided.')
        _thread.interrupt_main()
        return
      while True:
        try:
          self._establish_connection(conn)
        except:
          logging.exception('Failed to send local_reg keep alive to the AC.')
        self._json['local_reg']['notify'] = int(
            self._data.commands_queue.qsize() > 0 or self.run_lock.wait(self._KEEP_ALIVE_INTERVAL))

class QueryStatusThread(threading.Thread):
  """Thread to preiodically query the status of all properties.
  
  After start-up, essentailly all updates should be pushed to the server due
  to the keep alive, so this is just a belt and suspenders.
  """
  
  _STATUS_UPDATE_INTERVAL = 600.0
  _WAIT_FOR_EMPTY_QUEUE = 10.0

  def __init__(self, data: Data, device_controller: DeviceController):
    super(QueryStatusThread, self).__init__(name='Query Status thread')
    self._data = data
    self._device_controller = device_controller

  def run(self) -> None:
    while True:
      # In case the AC is stuck, and not fetching commands, avoid flooding
      # the queue with status updates.
      while self._data.commands_queue.qsize() > 10:
        time.sleep(self._WAIT_FOR_EMPTY_QUEUE)
      self._device_controller.queue_status()
      if _keep_alive:
        with _keep_alive.run_lock:
          _keep_alive.run_lock.notify()
      time.sleep(self._STATUS_UPDATE_INTERVAL)

class HTTPRequestHandler(BaseHTTPRequestHandler):
  """Handler for AC related HTTP requests."""
  def __init__(self, device_controller: DeviceController):
    super().__init__()
    _query_handlers = QueryHandlers(config, data, device_controller, 
                                  self._write_response)
    self._HANDLERS_MAP = {
      '/hisense/status': _query_handlers.get_status_handler,
      '/hisense/command': _query_handlers.queue_command_handler,
      '/local_lan/key_exchange.json': _query_handlers.key_exchange_handler,
      '/local_lan/commands.json': _query_handlers.command_handler,
      '/local_lan/property/datapoint.json': _query_handlers.property_update_handler,
      '/local_lan/property/datapoint/ack.json': _query_handlers.property_update_handler,
      '/local_lan/node/property/datapoint.json': _query_handlers.property_update_handler,
      '/local_lan/node/property/datapoint/ack.json': _query_handlers.property_update_handler,
      # TODO: Handle these if needed.
      # '/local_lan/node/conn_status.json': _query_handlers.connection_status_handler,
      # '/local_lan/connect_status': _query_handlers.module_request_handler,
      # '/local_lan/status.json': _query_handlers.setup_device_details_handler,
      # '/local_lan/wifi_scan.json': _query_handlers.module_request_handler,
      # '/local_lan/wifi_scan_results.json': _query_handlers.module_request_handler,
      # '/local_lan/wifi_status.json': _query_handlers.module_request_handler,
      # '/local_lan/regtoken.json': _query_handlers.module_request_handler,
      # '/local_lan/wifi_stop_ap.json': _query_handlers.module_request_handler,
    }

  def _queue_command(self, path: str, query: dict, data: dict):
      self._query_handlers.queue_command_handler(path, query, data)
      with _keep_alive.run_lock:
        _keep_alive.run_lock.notify()

  def _write_response(self, status: HTTPStatus, response: str):
    self.do_HEAD(status)
    self.wfile.write(response)

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
  parsed_args = ParseArguments()  # type: argparse.Namespace

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
  logger.setLevel(parsed_args.log_level)
  logger.addHandler(logging_handler)

  config = Config() # TODO: Why it is not used?
  if parsed_args.device_type == 'ac':
    data = Data(properties=AcProperties())
  elif parsed_args.device_type == 'fgl':
    data = Data(properties=FglProperties())
  elif parsed_args.device_type == 'fgl_b':
    data = Data(properties=FglBProperties())
  elif parsed_args.device_type == 'humidifier':
    data = Data(properties=HumidifierProperties())
  else:
    sys.exit(1)  # Should never get here.

  device_controller = DeviceController(data)

  if parsed_args.mqtt_host:
    mqtt_topics = {'pub' : '/'.join((parsed_args.mqtt_topic, '{}', 'status')),
                  'sub' : '/'.join((parsed_args.mqtt_topic, '{}', 'command'))}
    mqtt_client = MqttClient(parsed_args.mqtt_client_id, data, mqtt_topics, device_controller)
    if parsed_args.mqtt_user:
      mqtt_client.username_pw_set(*parsed_args.mqtt_user.split(':',1))
    mqtt_client.connect(parsed_args.mqtt_host, parsed_args.mqtt_port)
    mqtt_client.loop_start()

  _keep_alive = None  # type: typing.Optional[KeepAliveThread]

  query_status = QueryStatusThread(data, device_controller)
  query_status.start()

  _keep_alive = KeepAliveThread(parsed_args.ip, parsed_args.port, data)
  _keep_alive.start()

  request_handler = HTTPRequestHandler(device_controller)
  httpd = HTTPServer(('', parsed_args.port), request_handler)
  try:
    httpd.serve_forever()
  except KeyboardInterrupt:
    pass
  httpd.server_close()
