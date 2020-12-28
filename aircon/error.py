class Error(Exception):
  """Error class for AC handling."""
  pass


class KeyIdReplaced(Exception):
  """Error class for key id replacement"""

  def __init__(self, title, message):
    self.title = title
    self.message = message
