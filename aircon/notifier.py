import aiohttp
import asyncio
import concurrent
from dataclasses import dataclass
from http import HTTPStatus
import json
import logging
import socket
import sys
from tenacity import retry, retry_if_exception_type, wait_incrementing
import time
import threading

from .aircon import Device

if sys.version_info < (3, 8):
  TimeoutError = concurrent.futures.TimeoutError
else:
  TimeoutError = asyncio.exceptions.TimeoutError


@dataclass
class _NotifyConfiguration:
  device: Device
  headers: dict
  last_timestamp: int


class Notifier:
  _KEEP_ALIVE_INTERVAL = 10.0
  _TIME_TO_HANDLE_REQUESTS = 100e-3

  def __init__(self, port: int):
    self._configurations = []
    self._condition = asyncio.Condition()

    self._running = False

    local_ip = self._get_local_ip()
    self._json = {'local_reg': {'ip': local_ip, 'notify': 0, 'port': port, 'uri': '/local_lan'}}

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

  def register_device(self, device: Device):
    if device not in (conf.device for conf in self._configurations):
      headers = {
          'Accept': 'application/json',
          'Connection': 'keep-alive',
          'Content-Type': 'application/json',
          'Host': device.ip_address,
          'Accept-Encoding': 'gzip'
      }
      self._configurations.append(_NotifyConfiguration(device, headers, 0))

  async def _notify(self):
    async with self._condition:
      self._condition.notify_all()

  def notify(self):
    loop = asyncio.get_event_loop()
    asyncio.run_coroutine_threadsafe(self._notify(), loop)

  async def start(self, session: aiohttp.ClientSession):
    self._running = True
    async with self._condition:
      while self._running:
        queues_empty = True
        for config in self._configurations:
          try:
            now = time.time()
            queue_size = config.device.commands_queue.qsize()
            if queue_size > 1:
              queues_empty = False
            if now - config.last_timestamp >= self._KEEP_ALIVE_INTERVAL or (queue_size > 0 and config.device.available):
              config.last_timestamp = now
              await self._perform_request(session, config)
          except:
            logging.exception('[KeepAlive] Failed to send local_reg keep alive to the AC.')
            config.device.available = False
          else:
            config.device.available = True
        if queues_empty:
          logging.debug('[KeepAlive] Waiting for notification or timeout')
          try:
            await asyncio.wait_for(self._condition.wait(), timeout=self._KEEP_ALIVE_INTERVAL)
          except TimeoutError:
            pass
        else:
          # give some time to clean up the queues
          await asyncio.sleep(self._TIME_TO_HANDLE_REQUESTS)

  async def stop(self):
    self._running = False
    await self._notify()

  @retry(retry=retry_if_exception_type(ConnectionError),
         wait=wait_incrementing(start=0.5, increment=1.5, max=10))
  async def _perform_request(self, session: aiohttp.ClientSession,
                             config: _NotifyConfiguration) -> None:
    method = 'PUT' if config.device.available else 'POST'
    self._json['local_reg']['notify'] = int(config.device.commands_queue.qsize() > 0)
    url = 'http://{}/local_reg.json'.format(config.device.ip_address)
    try:
      logging.debug('[KeepAlive] Sending {} {} {}'.format(method, url, json.dumps(self._json)))
      async with session.request(method, url, json=self._json, headers=config.headers) as resp:
        if resp.status != HTTPStatus.ACCEPTED.value:
          resp_data = await resp.text()
          logging.error('[KeepAlive] Sending local_reg failed: {}, {}'.format(
              resp.status, resp_data))
          raise ConnectionError('Sending local_reg failed: {}, {}'.format(resp.status, resp_data))
    except:
      config.device.available = False
      raise
    else:
      config.device.available = True
