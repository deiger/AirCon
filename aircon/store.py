from dataclasses import dataclass
import logging
import queue
import threading

from . import aircon
from .mqtt_client import MqttClient
from .properties import Properties

@dataclass
class Data:
  """The current data store: commands, updates and properties."""
  commands_queue = queue.Queue()
  commands_seq_no = 0
  commands_seq_no_lock = threading.Lock()
  updates_seq_no = 0
  updates_seq_no_lock = threading.Lock()
  properties: Properties
  properties_lock = threading.Lock()
  _mqtt_client: MqttClient

  def get_property(self, name: str):
    """Get a stored property."""
    with self.properties_lock:
      return getattr(self.properties, name)

  def update_property(self, name: str, value) -> None:
    """Update the stored properties, if changed."""
    with self.properties_lock:
      old_value = getattr(self.properties, name)
      if value != old_value:
        setattr(self.properties, name, value)
        logging.debug('Updated properties: %s' % self.properties)
      self._mqtt_client.mqtt_publish_update(name, value)
