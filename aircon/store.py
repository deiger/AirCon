from dataclasses import dataclass
import logging
import queue
import threading
from typing import Callable

from . import aircon
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
  change_listener: Callable[[str, str], None] = None

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
      if (self.change_listener != None):
        self.change_listener(name, value)
