import aiohttp
import asyncio
import concurrent
from dataclasses import dataclass
from http import HTTPStatus
import json
import logging
import socket
import sys
from tenacity import retry, retry_if_exception_type, wait_exponential
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
        queue_sizes = await asyncio.gather(*(self._perform_request(session=session, config=config)
                                             for config in self._configurations))
        if max(queue_sizes) <= 1:
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

  @staticmethod
  def _run_after_failure(retry_state):
    config = retry_state.kwargs['config']
    config.device.available = False
    return 0

  @retry(retry=retry_if_exception_type(ConnectionError),
         retry_error_callback=_run_after_failure,
         wait=wait_exponential(min=0.5, multiplier=1.5, max=10))
  async def _perform_request(self, session: aiohttp.ClientSession,
                             config: _NotifyConfiguration) -> int:
    now = time.time()
    queue_size = config.device.commands_queue.qsize()
    if (queue_size == 0 or
        not config.device.available) and now - config.last_timestamp < self._KEEP_ALIVE_INTERVAL:
      return 0
    method = 'PUT' if config.device.available else 'POST'
    self._json['local_reg']['notify'] = int(config.device.commands_queue.qsize() > 0)
    url = 'http://{}/local_reg.json'.format(config.device.ip_address)
    logging.debug('[KeepAlive] Sending {} {} {}'.format(method, url, json.dumps(self._json)))
    async with session.request(method, url, json=self._json, headers=config.headers) as resp:
      if resp.status != HTTPStatus.ACCEPTED.value:
        resp_data = await resp.text()
        logging.error('[KeepAlive] Sending local_reg failed: {}, {}'.format(resp.status, resp_data))
        raise ConnectionError('Sending local_reg failed: {}, {}'.format(resp.status, resp_data))
    config.last_timestamp = now
    config.device.available = True
    return queue_size
