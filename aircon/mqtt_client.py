#!/usr/bin/env python3.7
"""
MQTT client for the air conditioner module server.
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
