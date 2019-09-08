#!/usr/bin/env python3.7
"""
Small command line program to query HiSense servers.
Generates a small config file, to control the AC locally.

After configuring the AC from your phone, pass the username, password
and application type to this script, in order to be able to control
the device locally.

Note that this script needs to be run only once. The generated config
file needs to be passed to the hisense server script, to continuously
control the AC.

The --app flag depends on your AC:
Beko: beko-eu
Hisense (EU): oem-eu
Hisense (US): oem-us
Neutral (EU): mid-eu
Neutral (US): mid-us
Tornado: tornado-us
Westinghouse: wwh-us
Winia: winia-us
York: york-us
"""
__author__ = 'droreiger@gmail.com (Dror Eiger)'

import argparse
import base64
import json
import logging
import ssl
import sys
from http.client import HTTPSConnection

_HISENSE_DOMAIN = 'aylanetworks.com'
_HISENSE_USER_SERVER = 'user-field.' + _HISENSE_DOMAIN
_HISENSE_DEVICES_SERVER = 'ads-field.' + _HISENSE_DOMAIN
_SECRET_MAP = {
  'oem-us': b'\x1dgAPT\xd1\xa9\xec\xe2\xa2\x01\x19\xc0\x03X\x13j\xfc\xb5\x91',
  'mid-us': b'\xdeCx\xbe\x0cq8\x0b\x99\xb4Z\x93>\xfc\xcc\x9ag\x98\xf8\x14',
  'tornado-us': b'\x87O\xf2.&;X\xfb\xf6L\xfdRq\'\x0f\t6\x0c\xfd)',
  'wwh-us': b'(\xcb9w\xc5\xc9\xb7\xab{*k8T!Yb\xaa\xcf\xd0\x85',
  'winia-us': b'\xeb_\xce\xb2\xc6\xff`\xa9\xfa\xa8r\x1c\x0bH\xf8\xe27\xa7U\xec',
  'york-us': b'\xc6A\x7fHyV<\xb2\xa2\xde<\x1f{c\xa9\rt\x9fy\xef',
  'beko-eu': b'\xa9C\n\xdb\xf7+\x01\xe2X\ne\x85\x06\x89\xaa\x88ZP+\x07>~s{\xd3\x1f\x05\x91&\x8c\x81\x84&\xe11\xef=s"*\xa4',
  'oem-eu': b'a\x1ez\xf5\xc4\x0f\x18~\xe5\xeb\xb1\x9f\xe4\xf5&B\xfe#\x88\xcb>\x06O,y\xc1\x06c\x9d\x99J\xc2x\xac\xeb\x82\x93\xe5\r\x89d',
  'mid-eu': b'\x05$\xe6\xecW\xa3\xd1B\xa0\x84\xab*\xf0\x04\x80\xce\xae\xe5`\xc4>w\xf8\xc4\xf3X\xf6<\xd2\xd2I\x14!\xd0\x98\xed\xf2\xab\xae\xc6\x03',
  'haxxair': b'\xd8\xaf\x89--\x00\xabI\x93\x83j\xab\x9acX\xac^\x90f;',
  'field-us': b'\xc8b\x08\xfa\xce8\xf8\xf1\x81\xa5\x81\x8fX\xb4\x80\xc0\xdc\xf5\ny',
}
_SECRET_ID_MAP = {
  'haxxair': 'HAXXAIR',
  'field-us': 'pactera-field-f624d97f-us',
}
_USER_AGENT = 'Dalvik/2.1.0 (Linux; U; Android 9.0; SM-G850F Build/LRX22G)'

if __name__ == '__main__':
  arg_parser = argparse.ArgumentParser(
      description='Command Line to query HiSense server.',
      allow_abbrev=False)
  arg_parser.add_argument('-a', '--app', required=True,
                          choices=set(_SECRET_MAP),
                          help='The app used for the login.')
  arg_parser.add_argument('-u', '--user', required=True,
                          help='Username for the app login.')
  arg_parser.add_argument('-p', '--passwd', required=True,
                          help='Password for the app login.')
  arg_parser.add_argument('-d', '--device', default=None,
                          help='Device name to fetch data for. If not set, takes the first.')
  arg_parser.add_argument('--config', required=True,
                          help='Config file to write to.')
  args = arg_parser.parse_args()
  logging_handler = logging.StreamHandler(stream=sys.stderr)
  logging_handler.setFormatter(
      logging.Formatter(fmt='{levelname[0]}{asctime}.{msecs:03.0f}  '
                        '{filename}:{lineno}] {message}',
                         datefmt='%m%d %H:%M:%S', style='{'))
  logger = logging.getLogger()
  logger.setLevel('INFO')
  logger.addHandler(logging_handler)
  if args.app in _SECRET_ID_MAP:
    app_prefix = _SECRET_ID_MAP[args.app]
  else:
    app_prefix = 'a-Hisense-{}-field'.format(args.app)
  app_id = app_prefix + '-id'
  secret = base64.b64encode(_SECRET_MAP[args.app]).decode('utf-8').rstrip('=').replace('+', '-').replace('/', '_')
  app_secret = '-'.join((app_prefix, secret))
  ssl_context = ssl.SSLContext()
  ssl_context.verify_mode = ssl.CERT_NONE
  ssl_context.check_hostname = False
  ssl_context.load_default_certs()
  conn = HTTPSConnection(_HISENSE_USER_SERVER, context=ssl_context)
  query = {
    'user': {
      'email': args.user,
      'password': args.passwd,
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
    'Host': _HISENSE_USER_SERVER,
    'Accept-Encoding': 'gzip'
  }
  conn.request('POST', '/users/sign_in.json', body=json.dumps(query), headers=headers)
  resp = conn.getresponse()
  if resp.status != 200:
    logging.error('Failed to login to Hisense server: %r', resp)
    sys.exit(1)
  tokens = json.loads(resp.read())
  conn.close()
  conn = HTTPSConnection(_HISENSE_DEVICES_SERVER, context=ssl_context)
  headers = {
    'Accept': 'application/json',
    'Connection': 'Keep-Alive',
    'Authorization': 'auth_token ' + tokens['access_token'],
    'User-Agent': _USER_AGENT,
    'Host': _HISENSE_DEVICES_SERVER,
    'Accept-Encoding': 'gzip'
  }
  conn.request('GET', '/apiv1/devices.json', headers=headers)
  resp = conn.getresponse()
  if resp.status != 200:
    logging.error('Failed to get devices data from Hisense server: %r', resp)
    sys.exit(1)
  devices = json.loads(resp.read())
  if not devices:
    logging.error('No device is configured! Please configure a device first.')
    sys.exit(1)
  logging.info('Found devices: %r', devices)
  if args.device:
    for device in devices:
      device = device
      if device['device']['product_name'] == args.device:
        break
    else:
      logging.error('No device named "%s" was found!', args.device)
      sys.exit(1)
  else:
    device = devices[0]
  dsn = device['device']['dsn']
  conn.request('GET', '/apiv1/dsns/{}/lan.json'.format(dsn), headers=headers)
  resp = conn.getresponse()
  if resp.status != 200:
    logging.error('Failed to get device data from Hisense server: %r', resp)
    sys.exit(1)
  lanip = json.loads(resp.read())['lanip']
  conn.close()
  config = {
    'lanip_key': lanip['lanip_key'],
    'lanip_key_id': lanip['lanip_key_id'],
    'random_1': '',
    'time_1': 0,
    'random_2': '',
    'time_2': 0
  }
  with open(args.config, 'w') as f:
    f.write(json.dumps(config))
