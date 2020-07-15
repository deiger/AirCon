import aiohttp
import asyncio
import concurrent
from dataclasses import dataclass
from http import HTTPStatus
import json
import logging
import socket
from tenacity import retry, retry_if_exception_type, wait_incrementing
import time
import threading

from .aircon import BaseDevice

@dataclass
class _NotifyConfiguration:
  device: BaseDevice
  headers: dict
  alive: bool
  last_timestamp: int

class Notifier:
  _KEEP_ALIVE_INTERVAL = 10.0

  def __init__(self, port: int):
    self._configurations = []
    self._condition = asyncio.Condition()

    local_ip = self._get_local_ip()
    self._json = {
      'local_reg': {
        'ip': local_ip,
        'notify': 0,
        'port': port,
        'uri': "/local_lan"
      }
    }

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

  def register_device(self, device: BaseDevice):
    if (not device in self._configurations):
      headers = {
        'Accept': 'application/json',
        'Connection': 'keep-alive',
        'Content-Type': 'application/json',
        'Host': device.ip_address,
        'Accept-Encoding': 'gzip'
      }
      self._configurations.append(_NotifyConfiguration(device, headers, False, 0))

  async def _notify(self):
    async with self._condition:
      self._condition.notify_all()

  def notify(self):
    loop = asyncio.get_event_loop()
    asyncio.run_coroutine_threadsafe(self._notify(), loop)

  async def start(self):
    async with self._condition:
      while True:
        queues_empty = True
        try:
          for entry in self._configurations:
            now = time.time()
            queue_size = entry.device.commands_queue.qsize()
            if queue_size > 0:
              queues_empty = False
            if now - entry.last_timestamp >= self._KEEP_ALIVE_INTERVAL or queue_size > 0:
              await self._perform_request(entry)
              entry.last_timestamp = now
        except:
          logging.exception('[KeepAlive] Failed to send local_reg keep alive to the AC.')
        if queues_empty:
          logging.debug('[KeepAlive] Waiting for notification or timeout')
          try:
            await asyncio.wait_for(self._condition.wait(), timeout=self._KEEP_ALIVE_INTERVAL)
            #await self._wait_on_condition_with_timeout(self._condition, self._KEEP_ALIVE_INTERVAL)
          except concurrent.futures.TimeoutError:
            pass

  @retry(retry=retry_if_exception_type(ConnectionError), wait=wait_incrementing(start=0.5, increment=1.5, max=10))
  async def _perform_request(self, config: _NotifyConfiguration) -> None:
    method = 'PUT' if config.alive else 'POST'
    self._json['local_reg']['notify'] = int(config.device.commands_queue.qsize() > 0)
    url = 'http://{}/local_reg.json'.format(config.device.ip_address)
    try:
      async with aiohttp.ClientSession(conn_timeout=5.0) as session:
        logging.debug('[KeepAlive] Sending {} {} {}'.format(method, url, json.dumps(self._json)))
        async with session.request(method, url, json=self._json, headers=config.headers) as resp:
          if resp.status != HTTPStatus.ACCEPTED.value:
            resp_data = await resp.text()
            logging.error('[KeepAlive] Sending local_reg failed: {}, {}'.format(resp.status, resp_data))
            raise ConnectionError('Sending local_reg failed: {}, {}'.format(resp.status, resp_data))
    except:
      config.alive = False
      raise
    config.alive = True

  # @staticmethod
  # async def _wait_on_condition_with_timeout(condition: asyncio.Condition, timeout: float) -> bool:
  #   loop = asyncio.get_event_loop()

  #   # Create a future that will be triggered by either completion or timeout.
  #   waiter = loop.create_future()

  #   # Callback to trigger the future. The varargs are there to consume and void any arguments passed.
  #   # This allows the same callback to be used in loop.call_later and wait_task.add_done_callback,
  #   # which automatically passes the finished future in.
  #   def release_waiter(*_):
  #     if not waiter.done():
  #       waiter.set_result(None)

  #   # Set up the timeout
  #   timeout_handle = loop.call_later(timeout, release_waiter)

  #   # Launch the wait task
  #   wait_task = loop.create_task(condition.wait())
  #   wait_task.add_done_callback(release_waiter)

  #   try:
  #     await waiter  # Returns on wait complete or timeout
  #     if wait_task.done():
  #       return True
  #     else:
  #       raise asyncio.TimeoutError()

  #   except (asyncio.TimeoutError, asyncio.CancelledError):
  #     # If timeout or cancellation occur, clean up, cancel the wait, let it handle the cancellation,
  #     # then re-raise.
  #     wait_task.remove_done_callback(release_waiter)
  #     wait_task.cancel()
  #     await asyncio.wait([wait_task])
  #     raise

  #   finally:
  #     timeout_handle.cancel()
