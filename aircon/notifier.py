#!/usr/bin/env python3.7
"""
Notifier for the air conditioner module server.
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
