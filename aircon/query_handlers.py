from aiohttp import web
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

from .config import Config, Encryption
from .aircon import Device
from .error import Error, KeyIdReplaced


class QueryHandlers:

  def __init__(self, devices: [Device]):
    self._devices_map = {}
    for device in devices:
      self._devices_map[device.ip_address] = device

  async def key_exchange_handler(self, request: web.Request) -> web.Response:
    """Handles a key exchange.
    Accepts the AC's random and time and pass its own.
    Note that a key encryption component is the lanip_key, mapped to the
    lanip_key_id provided by the AC. This secret part is provided by HiSense
    server. Fortunately the lanip_key_id (and lanip_key) are static for a given
    AC.
    """
    updated_keys = {}
    post_data = await request.text()
    data = json.loads(post_data)
    try:
      key = data['key_exchange']
      if key['ver'] != 1 or key['proto'] != 1 or key.get('sec'):
        logging.error(f'Invalid key exchange: {data}')
        raise web.HTTPBadRequest(reason=f'Invalid key exchange: {data}')
      updated_keys = self._devices_map[request.remote].update_key(key)
    except KeyIdReplaced as e:
      logging.error(f'{e.title}\n{e.message}')
      return web.Response(status=HTTPStatus.NOT_FOUND.value, reason=f'{e.title}\n{e.message}')
    return web.json_response(updated_keys)

  async def command_handler(self, request: web.Request) -> web.Response:
    """Handles a command request.
    Request arrives from the AC. takes a command from the queue,
    builds the JSON, encrypts and signs it, and sends it to the AC.
    """
    command = {}
    device = self._devices_map[request.remote]
    command['seq_no'] = device.get_command_seq_no()
    try:
      command_entry = device.commands_queue.get_nowait()
      command['data'], property_updater = command_entry.command, command_entry.updater
    except queue.Empty:
      command['data'], property_updater = {}, None
    if property_updater:
      property_updater()  #TODO: should be async as well?
    return web.json_response(self._encrypt_and_sign(device, command))

  async def property_update_handler(self, request: web.Request) -> web.Response:
    """Handles a property update request.
    Decrypts, validates, and pushes the value into the local properties store.
    """
    device = self._devices_map[request.remote]
    post_data = await request.text()
    data = json.loads(post_data)
    try:
      update = self._decrypt_and_validate(device, data)
    except Error:
      logging.exception('Failed to parse property.')
      return web.Response(status=HTTPStatus.BAD_REQUEST.value, reason='Failed to parse property.')
    response = web.Response()
    if not device.is_update_valid(update['seq_no']):
      return response
    try:
      if not update['data']:
        logging.info('Unsupported update message = {}'.format(update['seq_no']))
        return response
      name = update['data']['name']
      data_type = device.get_property_type(name)
      value = data_type(update['data']['value'])
      device.update_property(name, value)
    except Exception as ex:
      logging.error('Failed to handle {}. Exception = {}'.format(update, ex))
      #TODO: Should return internal error?
    return response

  async def get_status_handler(self, request: web.Request) -> web.Response:
    """Handles get status request (by a smart home hub).
    Returns the current internally stored state of the AC.
    """
    devices = []
    for device in self._devices_map.values():
      if 'device_ip' in request.query.keys() and device.ip_address != request.query['device_ip']:
        continue
      devices.append({'ip': device.ip_address, 'props': device.get_all_properties().to_dict()})
    return web.json_response({'devices': devices})

  async def queue_command_handler(self, request: web.Request) -> web.Response:
    """Handles queue command request (by a smart home hub).
    """
    device = self._devices_map.get(request.query.get('device_ip'))
    if not device:
      if len(self._devices_map) == 1:
        device = list(self._devices_map.values())[0]
      else:
        raise web.HTTPBadRequest(reason=f'Device "{request.query.get("device_ip")}" not found.')
    try:
      device.queue_command(request.query['property'], request.query['value'])
    except Exception as ex:
      logging.exception('Failed to queue command.')
      raise web.HTTPBadRequest(f'Failed to queue command:\n{ex!r}')
    return web.json_response({'queued_commands': device.commands_queue.qsize()})

  def _encrypt_and_sign(self, device: Device, data: dict) -> dict:
    text = json.dumps(data)
    logging.debug('Encrypting: {}'.format(text))
    text = text.encode('utf-8')
    encryption = device.get_app_encryption()
    return {
        "enc": base64.b64encode(encryption.cipher.encrypt(self.pad(text))).decode('utf-8'),
        "sign": base64.b64encode(Encryption.hmac_digest(encryption.sign_key, text)).decode('utf-8')
    }

  def _decrypt_and_validate(self, device: Device, data: dict) -> dict:
    encryption = device.get_dev_encryption()
    text = self.unpad(encryption.cipher.decrypt(base64.b64decode(data['enc'])))
    sign = base64.b64encode(Encryption.hmac_digest(encryption.sign_key, text)).decode('utf-8')
    if sign != data['sign']:
      raise Error(f'Invalid signature for:\n{text.decode("utf-8")}!')
    logging.info('Decrypted: %s', text.decode('utf-8'))
    try:
      return json.loads(text.decode('utf-8'))
    except Exception as ex:
      raise Error(f'Failed to decode message, {ex!r}:\n{text.decode("utf-8")}')

  @staticmethod
  def pad(data: bytes):
    """Zero padding for AES data encryption (non standard)."""
    new_size = math.ceil(len(data) / AES.block_size) * AES.block_size
    return data.ljust(new_size, bytes([0]))

  @staticmethod
  def unpad(data: bytes):
    """Remove Zero padding for AES data encryption (non standard)."""
    return data.rstrip(bytes([0]))
