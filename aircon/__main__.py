import aiohttp
from aiohttp import web
import argparse
import asyncio
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
import textwrap
import threading
import time
import _thread
from urllib.parse import parse_qs, urlparse, ParseResult

from .app_mappings import SECRET_MAP
from .config import Config
from .error import Error
from .aircon import BaseDevice, AcDevice, FglDevice, FglBDevice, HumidifierDevice
from .discovery import perform_discovery
from .mqtt_client import MqttClient
from .notifier import Notifier
from .query_handlers import QueryHandlers


async def query_status_worker(devices: [BaseDevice]):
  _STATUS_UPDATE_INTERVAL = 600.0
  _WAIT_FOR_EMPTY_QUEUE = 10.0
  while True:
    # In case the AC is stuck, and not fetching commands, avoid flooding
    # the queue with status updates.
    for device in devices:
      while device.commands_queue.qsize() > 10:
        await asyncio.sleep(_WAIT_FOR_EMPTY_QUEUE)
      device.queue_status()
    await asyncio.sleep(_STATUS_UPDATE_INTERVAL)


def ParseArguments() -> argparse.Namespace:
  """Parse command line arguments."""
  arg_parser = argparse.ArgumentParser(description='JSON server for HiSense air conditioners.',
                                       allow_abbrev=False)
  arg_parser.add_argument('--log_level',
                          default='WARNING',
                          choices={'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG'},
                          help='Minimal log level.')
  subparsers = arg_parser.add_subparsers(dest='cmd', help='Determines what server should do')
  subparsers.required = True

  parser_run = subparsers.add_parser('run', help='Runs the server to control the device')
  parser_run.add_argument('-p', '--port', required=True, type=int, help='Port for the server.')
  group_device = parser_run.add_argument_group('Device', 'Arguments that are related to the device')
  group_device.add_argument('--config', required=True, action='append', help='LAN Config file.')
  group_device.add_argument('--type',
                            required=True,
                            action='append',
                            choices={'ac', 'fgl', 'fgl_b', 'humidifier'},
                            help='Device type (for systems other than Hisense A/C).')

  group_mqtt = parser_run.add_argument_group('MQTT', 'Settings related to the MQTT')
  group_mqtt.add_argument('--mqtt_host', default=None, help='MQTT broker hostname or IP address.')
  group_mqtt.add_argument('--mqtt_port', type=int, default=1883, help='MQTT broker port.')
  group_mqtt.add_argument('--mqtt_client_id', default=None, help='MQTT client ID.')
  group_mqtt.add_argument('--mqtt_user', default=None, help='<user:password> for the MQTT channel.')
  group_mqtt.add_argument('--mqtt_topic', default='hisense_ac', help='MQTT topic.')
  group_mqtt.add_argument('--mqtt_discovery_prefix',
                          default='homeassistant',
                          help='MQTT discovery prefix for HomeAssistant.')

  parser_discovery = subparsers.add_parser('discovery', help='Runs the device discovery')
  parser_discovery.add_argument('app', choices=set(SECRET_MAP), help='The app used for the login.')
  parser_discovery.add_argument('user', help='Username for the app login.')
  parser_discovery.add_argument('passwd', help='Password for the app login.')
  parser_discovery.add_argument('-d',
                                '--device',
                                default=None,
                                help='Device name to fetch data for. If not set, takes all.')
  parser_discovery.add_argument('--prefix',
                                required=False,
                                default='config_',
                                help='Config file prefix.')
  parser_discovery.add_argument('--properties',
                                action='store_true',
                                help='Fetch the properties for the device.')
  return arg_parser.parse_args()


def setup_logger(log_level, use_stderr=False):
  if use_stderr:
    logging_handler = logging.StreamHandler(sys.stderr)
  elif sys.platform == 'linux':
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
                        datefmt='%m%d %H:%M:%S',
                        style='{'))
  logger = logging.getLogger()
  logger.setLevel(log_level)
  logger.addHandler(logging_handler)


async def setup_and_run_http_server(parsed_args, devices: [BaseDevice]):
  query_handlers = QueryHandlers(devices)
  app = web.Application()
  app.add_routes([
      web.get('/hisense/status', query_handlers.get_status_handler),
      web.get('/hisense/command', query_handlers.queue_command_handler),
      web.post('/local_lan/key_exchange.json', query_handlers.key_exchange_handler),
      web.get('/local_lan/commands.json', query_handlers.command_handler),
      web.post('/local_lan/property/datapoint.json', query_handlers.property_update_handler),
      web.post('/local_lan/property/datapoint/ack.json', query_handlers.property_update_handler),
      web.post('/local_lan/node/property/datapoint.json', query_handlers.property_update_handler),
      web.post('/local_lan/node/property/datapoint/ack.json',
               query_handlers.property_update_handler),
      # TODO: Handle these if needed.
      # '/local_lan/node/conn_status.json': query_handlers.connection_status_handler,
      # '/local_lan/connect_status': query_handlers.module_request_handler,
      # '/local_lan/status.json': query_handlers.setup_device_details_handler,
      # '/local_lan/wifi_scan.json': query_handlers.module_request_handler,
      # '/local_lan/wifi_scan_results.json': query_handlers.module_request_handler,
      # '/local_lan/wifi_status.json': query_handlers.module_request_handler,
      # '/local_lan/regtoken.json': query_handlers.module_request_handler,
      # '/local_lan/wifi_stop_ap.json': query_handlers.module_request_handler
  ])
  runner = web.AppRunner(app)
  await runner.setup()
  site = web.TCPSite(runner, port=parsed_args.port)
  await site.start()


async def mqtt_loop(mqtt_client: MqttClient):
  _MQTT_LOOP_TIMEOUT = 1
  while True:
    mqtt_client.loop()
    await asyncio.sleep(_MQTT_LOOP_TIMEOUT)


async def run(parsed_args):
  if len(parsed_args.type) != len(parsed_args.config):
    raise ValueError('Each device has to have specified type and config file')

  notifier = Notifier(parsed_args.port)
  devices = []
  for i in range(len(parsed_args.config)):
    with open(parsed_args.config[i], 'rb') as f:
      config = json.load(f)
    if parsed_args.type[i] == 'ac':
      device = AcDevice(config, notifier.notify)
    elif parsed_args.type[i] == 'fgl':
      device = FglDevice(config, notifier.notify)
    elif parsed_args.type[i] == 'fgl_b':
      device = FglBDevice(config, notifier.notify)
    elif parsed_args.type[i] == 'humidifier':
      device = HumidifierDevice(config, notifier.notify)
    else:
      logging.error('Unknown type of device: %s', parsed_args.type[i])
      sys.exit(1)  # Should never get here.
    notifier.register_device(device)
    devices.append(device)

  mqtt_client = None
  if parsed_args.mqtt_host:
    mqtt_topics = {
        'pub':
            '/'.join((parsed_args.mqtt_topic, '{}', '{}', 'status')),
        'sub':
            '/'.join((parsed_args.mqtt_topic, '{}', '{}', 'command')),
        'lwt':
            '/'.join((parsed_args.mqtt_topic, 'LWT')),
        'discovery':
            '/'.join((parsed_args.mqtt_discovery_prefix, 'climate', '{}', 'hvac', 'config'))
    }
    mqtt_client = MqttClient(parsed_args.mqtt_client_id, mqtt_topics, devices)
    if parsed_args.mqtt_user:
      mqtt_client.username_pw_set(*parsed_args.mqtt_user.split(':', 1))
    mqtt_client.will_set(mqtt_topics['lwt'], payload='offline', retain=True)
    mqtt_client.connect(parsed_args.mqtt_host, parsed_args.mqtt_port)
    mqtt_client.publish(mqtt_topics['lwt'], payload='online', retain=True)
    for device in devices:
      config = {
          'name': device.name,
          'unique_id': device.mac_address,
          'device': {
              'identifiers': [f'hisense_ac_{device.mac_address}'],
              'manufacturer': f'Hisense ({device.app})',
              'model': device.model,
              'name': device.name,
              'sw_version': device.sw_version
          },
          'current_temperature_topic': mqtt_topics['pub'].format(device.mac_address, 'f_temp_in'),
          'fan_mode_command_topic': mqtt_topics['sub'].format(device.mac_address, 't_fan_speed'),
          'fan_mode_state_topic': mqtt_topics['pub'].format(device.mac_address, 't_fan_speed'),
          'fan_modes': ['auto', 'lower', 'low', 'medium', 'high', 'higher'],
          'max_temp': '86',
          'min_temp': '61',
          'mode_command_topic': mqtt_topics['sub'].format(device.mac_address, 't_work_mode'),
          'mode_state_topic': mqtt_topics['pub'].format(device.mac_address, 't_work_mode'),
          'modes': ['off', 'fan_only', 'heat', 'cool', 'dry', 'auto'],
          'swing_modes': ['on', 'off'],
          'power_command_topic': mqtt_topics['sub'].format(device.mac_address, 't_power'),
          'power_state_topic': mqtt_topics['pub'].format(device.mac_address, 't_power'),
          'precision': 1.0,
          'swing_mode_command_topic': mqtt_topics['sub'].format(device.mac_address, 't_fan_power'),
          'swing_mode_state_topic': mqtt_topics['pub'].format(device.mac_address, 't_fan_power'),
          'temperature_command_topic': mqtt_topics['sub'].format(device.mac_address, 't_temp'),
          'temperature_state_topic': mqtt_topics['pub'].format(device.mac_address, 't_temp'),
          'temperature_unit': 'F'
      }
      mqtt_client.publish(mqtt_topics['discovery'].format(device.mac_address),
                          payload=json.dumps(config),
                          retain=True)
      device.add_property_change_listener(mqtt_client.mqtt_publish_update)

  async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(connect=5.0)) as session:
    await asyncio.gather(mqtt_loop(mqtt_client), setup_and_run_http_server(parsed_args, devices),
                         query_status_worker(devices), notifier.start(session))


def _escape_name(name: str):
  safe_name = name.replace(' ', '_').lower()
  return ''.join(x for x in safe_name if x.isalnum())


async def discovery(parsed_args):
  async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(connect=5.0)) as session:
    try:
      all_configs = await perform_discovery(session, parsed_args.app, parsed_args.user,
                                            parsed_args.passwd, parsed_args.device,
                                            parsed_args.properties)
    except Exception as e:
      print(f'Error occurred:\n{e!r}')
      sys.exit(1)

  for config in all_configs:
    properties_text = ''
    if 'properties' in config.keys():
      properties_text = f'Properties:\n{json.dumps(config["properties"], indent=2)}'
    print(
        textwrap.dedent(f"""Device {config['product_name']} has:
                              IP address: {config['lan_ip']}
                              lanip_key: {config['lanip_key']}
                              lanip_key_id: {config['lanip_key_id']}
                              {properties_text}
                              """))

    file_content = {
        'name': config['product_name'],
        'app': parsed_args.app,
        'model': config['oem_model'],
        'sw_version': config['sw_version'],
        'dsn': config['dsn'],
        'mac_address': config['mac'],
        'ip_address': config['lan_ip'],
        'lanip_key': config['lanip_key'],
        'lanip_key_id': config['lanip_key_id'],
    }
    with open(parsed_args.prefix + _escape_name(config['product_name']) + '.json', 'w') as f:
      f.write(json.dumps(file_content))


if __name__ == '__main__':
  parsed_args = ParseArguments()  # type: argparse.Namespace

  if parsed_args.cmd == 'run':
    setup_logger(parsed_args.log_level)
    asyncio.run(run(parsed_args))
  elif parsed_args.cmd == 'discovery':
    setup_logger(parsed_args.log_level, use_stderr=True)
    asyncio.run(discovery(parsed_args))
