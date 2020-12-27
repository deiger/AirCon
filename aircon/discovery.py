import base64
import gzip
from http.client import HTTPSConnection
import json
import logging
import ssl
import sys

from .app_mappings import *

_USER_AGENT = 'Dalvik/2.1.0 (Linux; U; Android 9.0; SM-G850F Build/LRX22G)'

def _sign_in(user: str, passwd: str, user_server: str, app_id: str, app_secret: str,
            ssl_context: ssl.SSLContext):
  conn = HTTPSConnection(user_server, context=ssl_context)
  query = {
    'user': {
      'email': user,
      'password': passwd,
      'application': {
        'app_id': app_id,
        'app_secret': app_secret
      }
    }
  }
  headers = {
    'Accept': 'application/json',
    'Connection': 'Keep-Alive',
    'Authorization': 'none',
    'Content-Type': 'application/json',
    'User-Agent': _USER_AGENT,
    'Host': user_server,
    'Accept-Encoding': 'gzip'
  }
  logging.debug('POST /users/sign_in.json, body=%r, headers=%r' % (json.dumps(query), headers))
  conn.request('POST', '/users/sign_in.json', body=json.dumps(query), headers=headers)
  resp = conn.getresponse()
  if resp.status != 200:
    logging.error('Failed to login to Hisense server:\nStatus %d: %r',
                  resp.status, resp.reason)
    sys.exit(1)
  resp_data = resp.read()
  try:
    resp_data = gzip.decompress(resp_data)
  except OSError:
    pass  # Not gzipped.
  try:
    tokens = json.loads(resp_data)
  except UnicodeDecodeError:
    logging.exception('Failed to parse login tokens to Hisense server:\nData: %r',
                      resp_data)
    sys.exit(1)
  conn.close()
  return tokens['access_token']

def _get_devices(devices_server: str, access_token: str, headers: dict, conn: HTTPSConnection):
  logging.debug('GET /apiv1/devices.json, headers=%r' % headers)
  conn.request('GET', '/apiv1/devices.json', headers=headers)
  resp = conn.getresponse()
  if resp.status != 200:
    logging.error('Failed to get devices data from Hisense server:\nStatus %d: %r',
                  resp.status, resp.reason)
    sys.exit(1)
  resp_data = resp.read()
  try:
    resp_data = gzip.decompress(resp_data)
  except OSError:
    pass  # Not gzipped.
  try:
    devices = json.loads(resp_data)
  except UnicodeDecodeError:
    logging.exception('Failed to parse devices data from Hisense server:\nData: %r',
                      resp_data)
    sys.exit(1)
  if not devices:
    logging.error('No device is configured! Please configure a device first.')
    sys.exit(1)
  return devices

def _get_lanip(dsn: str, headers: dict, conn: HTTPSConnection):
  conn.request('GET', '/apiv1/dsns/{}/lan.json'.format(dsn), headers=headers)
  resp = conn.getresponse()
  if resp.status != 200:
    logging.error('Failed to get device data from Hisense server: %r', resp)
    sys.exit(1)
  resp_data = resp.read()
  try:
    resp_data = gzip.decompress(resp_data)
  except OSError:
    pass  # Not gzipped.
  lanip = json.loads(resp_data)['lanip']
  return lanip

def _get_device_properties(dsn: str, headers: dict, conn: HTTPSConnection):
  conn.request('GET', '/apiv1/dsns/{}/properties.json'.format(dsn), headers=headers)
  resp = conn.getresponse()
  if resp.status != 200:
    logging.error('Failed to get properties data from Hisense server: %r', resp)
    sys.exit(1)
  resp_data = resp.read()
  try:
    resp_data = gzip.decompress(resp_data)
  except OSError:
    pass  # Not gzipped.
  return json.loads(resp_data)

def perform_discovery(app: str, user: str, passwd: str,
                     prefix: str, device_filter: str,
                     properties_filter: bool) -> dict:
  if app in SECRET_ID_MAP:
    app_prefix = SECRET_ID_MAP[app]
  else:
    app_prefix = 'a-Hisense-{}-field'.format(app)

  if app in SECRET_ID_EXTRA_MAP:
    app_id = '-'.join((app_prefix, SECRET_ID_EXTRA_MAP[app], 'id'))
  else:
    app_id = '-'.join((app_prefix, 'id'))

  secret = base64.b64encode(SECRET_MAP[app]).decode('utf-8').rstrip('=').replace('+', '-').replace('/', '_')
  app_secret = '-'.join((app_prefix, secret))

  # Extract the region from the app ID (and fallback to US)
  region = app[-2:]
  if region not in AYLA_USER_SERVERS:
    region = 'us'
  user_server = AYLA_USER_SERVERS[region]
  devices_server = AYLA_DEVICES_SERVERS[region]

  ssl_context = ssl.SSLContext()
  ssl_context.verify_mode = ssl.CERT_NONE
  ssl_context.check_hostname = False
  ssl_context.load_default_certs()

  access_token = _sign_in(user, passwd, user_server, app_id, app_secret, ssl_context)

  result = []
  conn = HTTPSConnection(devices_server, context=ssl_context)
  headers = {
    'Accept': 'application/json',
    'Connection': 'Keep-Alive',
    'Authorization': 'auth_token ' + access_token,
    'User-Agent': _USER_AGENT,
    'Host': devices_server,
    'Accept-Encoding': 'gzip'
  }
  devices = _get_devices(devices_server, access_token, headers, conn)
  logging.debug('Found devices: %r', devices)
  for device in devices:
    device_data = device['device']
    if device_filter and device_filter != device_data['product_name']:
      continue
    dsn = device_data['dsn']
    lanip = _get_lanip(dsn, headers, conn)
    properties_text = ''
    if properties_filter:
      props = _get_device_properties(dsn, headers, conn)
      device_data['properties'] = props
      properties_text = 'Properties:\n%s', json.dumps(props, indent=2)
    
    print('Device {} has:\nIP address: {}\nlanip_key: {}\nlanip_key_id: {}\n{}\n'.format(
                device_data['product_name'], device_data['lan_ip'],
                lanip['lanip_key'], lanip['lanip_key_id'], properties_text))
    
    device_data['lanip_key'] = lanip['lanip_key']
    device_data['lanip_key_id'] = lanip['lanip_key_id']
    result.append(device_data)
  conn.close()
  return result
