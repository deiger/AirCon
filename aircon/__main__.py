import argparse
import base64
from http import HTTPStatus
from http.client import HTTPConnection, InvalidURL
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import logging
import logging.handlers
import paho.mqtt.client as mqtt
from retry import retry
import signal
import socket
import sys
import threading
import time
import _thread
from urllib.parse import parse_qs, urlparse, ParseResult

from . import aircon
from .app_mappings import SECRET_MAP
from .config import Config
from .error import Error
from .aircon import BaseDevice, AcDevice, FglDevice, FglBDevice, HumidifierDevice
from .discovery import perform_discovery
from .mqtt_client import MqttClient
from .query_handlers import QueryHandlers

class KeepAliveThread(threading.Thread):
  """Thread to preiodically generate keep-alive requests."""
  
  _KEEP_ALIVE_INTERVAL = 10.0

  def __init__(self, host: str, port: int, device: BaseDevice):
    self.run_lock = threading.Condition()
    self._alive = False
    self._host = host
    self._port = port
    self._device = device
    local_ip = self._get_local_ip()
    self._headers = {
      'Accept': 'application/json',
      'Connection': 'keep-alive',
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

  def _get_local_ip(self):
    sock = None
    try:
      sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
      sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
      sock.connect(('10.255.255.255', 1))
      return sock.getsockname()[0]
    finally:
      if sock:
        sock.close()

  @retry(exceptions=ConnectionError, delay=0.5, max_delay=20, backoff=1.5, logger=logging)
  def _establish_connection(self, conn: HTTPConnection) -> None:
    method = 'PUT' if self._alive else 'POST'
    logging.debug('[KeepAlive] %s /local_reg.json %s', method, json.dumps(self._json))
    try:
      logging.debug('[KeepAlive] Sending data to %s. Data: %s', conn.host, json.dumps(self._json))
      conn.request(method, '/local_reg.json', json.dumps(self._json), self._headers)
      resp = conn.getresponse()
      if resp.status != HTTPStatus.ACCEPTED:
        raise ConnectionError('Recieved invalid response for local_reg: %d, %s', resp.status, resp.read())
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
          _thread.interrupt_main()
          return
        logging.debug('[KeepAlive] Waiting for notification or timeout %d', self._data.commands_queue.qsize())
        self._json['local_reg']['notify'] = int(
            self._device.commands_queue.qsize() > 0 or self.run_lock.wait(self._KEEP_ALIVE_INTERVAL))

class QueryStatusThread(threading.Thread):
  """Thread to preiodically query the status of all properties.
  
  After start-up, essentailly all updates should be pushed to the server due
  to the keep alive, so this is just a belt and suspenders.
  """
  
  _STATUS_UPDATE_INTERVAL = 600.0
  _WAIT_FOR_EMPTY_QUEUE = 10.0

  def __init__(self, device: BaseDevice):
    super(QueryStatusThread, self).__init__(name='Query Status thread')
    self._device = device

  def run(self) -> None:
    while True:
      # In case the AC is stuck, and not fetching commands, avoid flooding
      # the queue with status updates.
      while self._device.commands_queue.qsize() > 10:
        time.sleep(self._WAIT_FOR_EMPTY_QUEUE)
      self._device.queue_status()
      if _keep_alive:
        with _keep_alive.run_lock:
          logging.debug('QueryStatusThread triggered KeepAlive notify')
          _keep_alive.run_lock.notify()
      time.sleep(self._STATUS_UPDATE_INTERVAL)

def MakeHttpRequestHandlerClass(config: Config, device: BaseDevice):
  class HTTPRequestHandler(BaseHTTPRequestHandler):
    """Handler for AC related HTTP requests."""
    def __init__(self, request, client_address, server):
      self._query_handlers = QueryHandlers(config, device, self._write_response)
      self._HANDLERS_MAP = {
        '/hisense/status': self._query_handlers.get_status_handler,
        '/hisense/command': self._queue_command,
        '/local_lan/key_exchange.json': self._query_handlers.key_exchange_handler,
        '/local_lan/commands.json': self._query_handlers.command_handler,
        '/local_lan/property/datapoint.json': self._query_handlers.property_update_handler,
        '/local_lan/property/datapoint/ack.json': self._query_handlers.property_update_handler,
        '/local_lan/node/property/datapoint.json': self._query_handlers.property_update_handler,
        '/local_lan/node/property/datapoint/ack.json': self._query_handlers.property_update_handler,
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
      super(HTTPRequestHandler, self).__init__(request, client_address, server)

    def _queue_command(self, path: str, query: dict, data: dict):
        self._query_handlers.queue_command_handler(path, query, data)
        with _keep_alive.run_lock:
          logging.debug("_queue_command triggered KeepAlive notify")
          _keep_alive.run_lock.notify()

    def _write_response(self, status: HTTPStatus, response: str):
      self.do_HEAD(status)
      if (response != None):
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
          handler(parsed_url.path, query, {})
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
          handler(parsed_url.path, query, data)
          return
        except:
          logging.exception('Failed to parse property.')
      self.do_HEAD(HTTPStatus.NOT_FOUND)
  return HTTPRequestHandler

def ParseArguments() -> argparse.Namespace:
  """Parse command line arguments."""
  arg_parser = argparse.ArgumentParser(
      description='JSON server for HiSense air conditioners.',
      allow_abbrev=False)
  arg_parser.add_argument('--log_level', default='WARNING',
                          choices={'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'},
                          help='Minimal log level.')
  subparsers = arg_parser.add_subparsers(dest='cmd',
                                        help='Determines what server should do')
  subparsers.required = True

  parser_run = subparsers.add_parser('run', help='Runs the server to control the device')
  parser_run.add_argument('-p', '--port', required=True, type=int,
                          help='Port for the server.')
  parser_run.add_argument('--ip', required=True,
                          help='IP address for the AC.')
  parser_run.add_argument('--config', required=True,
                          help='LAN Config file.')
  parser_run.add_argument('--device_type', default='ac',
                          choices={'ac', 'fgl', 'fgl_b', 'humidifier'},
                          help='Device type (for systems other than Hisense A/C).')
  parser_run.add_argument('--mqtt_host', default=None,
                          help='MQTT broker hostname or IP address.')
  parser_run.add_argument('--mqtt_port', type=int, default=1883,
                          help='MQTT broker port.')
  parser_run.add_argument('--mqtt_client_id', default=None,
                          help='MQTT client ID.')
  parser_run.add_argument('--mqtt_user', default=None,
                          help='<user:password> for the MQTT channel.')
  parser_run.add_argument('--mqtt_topic', default='hisense_ac',
                          help='MQTT topic.')

  parser_discovery = subparsers.add_parser('discovery', help='Runs the device discovery')
  parser_discovery.add_argument('app',
                          choices=set(SECRET_MAP),
                          help='The app used for the login.')
  parser_discovery.add_argument('user', help='Username for the app login.')
  parser_discovery.add_argument('passwd', help='Password for the app login.')
  parser_discovery.add_argument('-d', '--device', default=None,
                          help='Device name to fetch data for. If not set, takes all.')
  parser_discovery.add_argument('--prefix', required=False, default='config_',
                          help='Config file prefix.')
  parser_discovery.add_argument('--properties', action='store_true',
                          help='Fetch the properties for the device.')
  return arg_parser.parse_args()

def setup_logger(log_level):
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
  logger.setLevel(log_level)
  logger.addHandler(logging_handler)

def run(parsed_args):
  config = Config(config_file=parsed_args.config)
  if parsed_args.device_type == 'ac':
    device = AcDevice()
  elif parsed_args.device_type == 'fgl':
    device = FglDevice()
  elif parsed_args.device_type == 'fgl_b':
    device = FglBDevice()
  elif parsed_args.device_type == 'humidifier':
    device = HumidifierDevice()
  else:
    sys.exit(1)  # Should never get here.

  if parsed_args.mqtt_host:
    mqtt_topics = {'pub' : '/'.join((parsed_args.mqtt_topic, '{}', 'status')),
                  'sub' : '/'.join((parsed_args.mqtt_topic, '{}', 'command'))}
    mqtt_client = MqttClient(parsed_args.mqtt_client_id, mqtt_topics, device)
    if parsed_args.mqtt_user:
      mqtt_client.username_pw_set(*parsed_args.mqtt_user.split(':',1))
    mqtt_client.connect(parsed_args.mqtt_host, parsed_args.mqtt_port)
    mqtt_client.loop_start()
    device.change_listener = mqtt_client.mqtt_publish_update

  global _keep_alive 
  _keep_alive = None  # type: typing.Optional[KeepAliveThread]

  query_status = QueryStatusThread(device)
  query_status.start()

  _keep_alive = KeepAliveThread(parsed_args.ip, parsed_args.port, device)
  _keep_alive.start()

  httpd = HTTPServer(('', parsed_args.port), MakeHttpRequestHandlerClass(config, device))
  try:
    httpd.serve_forever()
  except KeyboardInterrupt:
    pass
  finally:
    httpd.server_close()

def _escape_name(name: str):
  safe_name = name.replace(' ', '_').lower()
  return "".join(x for x in safe_name if x.isalnum())

def discovery(parsed_args):
  all_configs = perform_discovery(parsed_args.app, parsed_args.user, parsed_args.passwd, 
                   parsed_args.prefix, parsed_args.device, parsed_args.properties)
  for config in all_configs:
    file_content = {
      'lanip_key': config['lanip_key'],
      'lanip_key_id': config['lanip_key_id'],
      'random_1': '',
      'time_1': 0,
      'random_2': '',
      'time_2': 0
    }
    with open(parsed_args.prefix + _escape_name(config['product_name']) + '.json', 'w') as f:
      f.write(json.dumps(file_content))

if __name__ == '__main__':
  parsed_args = ParseArguments()  # type: argparse.Namespace
  setup_logger(parsed_args.log_level)

  if (parsed_args.cmd == 'run'):
    run(parsed_args)
  elif (parsed_args.cmd == 'discovery'):
    discovery(parsed_args)