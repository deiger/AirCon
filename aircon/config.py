#!/usr/bin/env python3.7
"""
Configuration for the air conditioner module server.
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
