from Crypto.Cipher import AES
from dataclasses import dataclass
from dataclasses_json import dataclass_json
import hmac
import random
import string
import time

from .error import KeyIdReplaced

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
  _lan_config: LanConfig
  app: Encryption
  dev: Encryption

  def __init__(self, lanip_key: str, lanip_key_id: str):
    self._lan_config = LanConfig(lanip_key, lanip_key_id, '', 0, '', 0)
    self._update_encryption()
    
  def update(self, key: dict):
    """Updates the stored lan config, and encryption data."""
    self._lan_config.random_1 = key['random_1']
    self._lan_config.time_1 = key['time_1']
    if key['key_id'] != self._lan_config.lanip_key_id:
      raise KeyIdReplaced('The key_id has been replaced!!', 
                         'Old ID was {}; new ID is {}.'.format(
                            self._lan_config.lanip_key_id, key['key_id']))
    self._lan_config.random_2 = ''.join(
        random.choices(string.ascii_letters + string.digits, k=16))
    self._lan_config.time_2 = time.monotonic_ns() % 2**40
    self._update_encryption()
    return {'random_2': self._lan_config.random_2,
          'time_2': self._lan_config.time_2}

  def _update_encryption(self):
    lanip_key = self._lan_config.lanip_key.encode('utf-8')
    random_1 = self._lan_config.random_1.encode('utf-8')
    random_2 = self._lan_config.random_2.encode('utf-8')
    time_1 = str(self._lan_config.time_1).encode('utf-8')
    time_2 = str(self._lan_config.time_2).encode('utf-8')
    self.app = Encryption(lanip_key, random_1 + random_2 + time_1 + time_2)
    self.dev = Encryption(lanip_key, random_2 + random_1 + time_2 + time_1)
