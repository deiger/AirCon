import base64
from Crypto.Cipher import AES
from http import HTTPStatus
import json
import math
import logging
import queue
import random
import string
import time
from typing import Callable

from . import aircon
from .config import Config, Encryption
from .aircon import BaseDevice
from .error import Error, KeyIdReplaced

class QueryHandlers:
  def __init__(self, device: BaseDevice, writer: Callable[[HTTPStatus, str], None]):
    self._device = device
    self._writer = writer

  def key_exchange_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles a key exchange.
    Accepts the AC's random and time and pass its own.
    Note that a key encryption component is the lanip_key, mapped to the
    lanip_key_id provided by the AC. This secret part is provided by HiSense
    server. Fortunately the lanip_key_id (and lanip_key) are static for a given
    AC.
    """
    updated_keys = {}
    try:
      key = data['key_exchange']
      if key['ver'] != 1 or key['proto'] != 1 or key.get('sec'):
        raise KeyError()
      updated_keys = self._device.update_key(key)
    except KeyError:
      logging.error('Invalid key exchange: %r', data)
      self._write_json(HTTPStatus.BAD_REQUEST)
      return
    except KeyIdReplaced as e:
      logging.error('{}\n{}'.format(e.title, e.message))
      self._write_json(HTTPStatus.NOT_FOUND)
      return
    self._write_json(HTTPStatus.OK, updated_keys)

  def command_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles a command request.
    Request arrives from the AC. takes a command from the queue,
    builds the JSON, encrypts and signs it, and sends it to the AC.
    """
    command = {}
    command['seq_no'] = self._device.get_command_seq_no()
    try:
      command['data'], property_updater = self._device.commands_queue.get_nowait()
    except queue.Empty:
      command['data'], property_updater = {}, None
    self._write_json(HTTPStatus.OK, self._encrypt_and_sign(command))
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
      self._write_json(HTTPStatus.BAD_REQUEST)
      return
    self._write_json(HTTPStatus.OK)
    if not self._device.is_update_valid():
      return
    try:
      if not update['data']:
        logging.debug('No value returned for seq_no %d, likely an unsupported property key.',
                      update['seq_no'])
        return
      name = update['data']['name']
      data_type = self._device.get_property_type(name)
      value = data_type(update['data']['value'])
      self._device.update_property(name, value)
    except:
      logging.exception('Failed to handle %s', update)

  def get_status_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles get status request (by a smart home hub).
    Returns the current internally stored state of the AC.
    """
    data = self._device.get_all_properties().to_dict()
    self._write_json(HTTPStatus.OK, data)

  def queue_command_handler(self, path: str, query: dict, data: dict) -> None:
    """Handles queue command request (by a smart home hub).
    """
    try:
      self._device.queue_command(query['property'][0], query['value'][0])
    except:
      logging.exception('Failed to queue command.')
      self._write_json(HTTPStatus.BAD_REQUEST)
      return
    self._write_json(HTTPStatus.OK, {'queued commands': self._device.commands_queue.qsize()})

  def _write_json(self, status: HTTPStatus, data: dict = None) -> None:
    """Send out the provided data dict as JSON."""
    logging.debug('Response:\n%s', json.dumps(data))
    self._writer(status, json.dumps(data).encode('utf-8'))

  def _encrypt_and_sign(self, data: dict) -> dict:
    text = json.dumps(data).encode('utf-8')
    logging.debug('Encrypting: %s', text.decode('utf-8'))
    encryption = self._device.get_app_encryption
    return {
      "enc": base64.b64encode(encryption.cipher.encrypt(self.pad(text))).decode('utf-8'),
      "sign": base64.b64encode(Encryption.hmac_digest(encryption.sign_key, text)).decode('utf-8')
    }

  def _decrypt_and_validate(self, data: dict) -> dict:
    encryption = self._device.get_dev_encryption
    text = self.unpad(encryption.cipher.decrypt(base64.b64decode(data['enc'])))
    sign = base64.b64encode(Encryption.hmac_digest(encryption.sign_key, text)).decode('utf-8')
    if sign != data['sign']:
      raise Error('Invalid signature for %s!' % text.decode('utf-8'))
    logging.info('Decrypted: %s', text.decode('utf-8'))
    return json.loads(text.decode('utf-8'))

  @staticmethod
  def pad(data: bytes):
    """Zero padding for AES data encryption (non standard)."""
    new_size = math.ceil(len(data) / AES.block_size) * AES.block_size
    return data.ljust(new_size, bytes([0]))

  @staticmethod
  def unpad(data: bytes):
    """Remove Zero padding for AES data encryption (non standard)."""
    return data.rstrip(bytes([0]))
