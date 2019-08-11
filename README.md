# HiSense Air Conditioners

This program implements the LAN API for HiSense WiFi Air Conditioner module, model AEH-W4B1.
The module is installed, for example, in Israel's Tornado-branded ACs, manufactured by HiSense Kelon.
The program may fit other HiSense modules as well, but I have not tried any.
It is not affiliated with either HiSense, any of it's subsidiaries, or any of its resellers.

## Prerequisites

1. Air Conditioner with HiSense AEH-W4B1 installed.
1. The LAN keys for the air conditioner. These are actually a bit difficult to come by. There is a 4 digit lanip_keyid, provided by the AC on every key exchange. This is mapped by HiSense' server to a lanip_key string. There are two options to fetch the right lanip_key for your AC:
   - Use a debug proxy tool like Fiddler, to sniff your phone's encrypted network traffic to the [ads-field.aylanetworks.com](ads-field.aylanetworks.com) server, and find the relevant keys. This would require installing network certificate to run a Man-in-the-Middle attack, which can be done only on older phones.
   - Run the HiSense (or Tornado) app on a *rooted* phone, add read the cache files. The apps stores two relevant files: `com.accontrol.tornado.america.hisense_preferences.xml` and `com.aylanetworks.aylasdk.aylacache.xml`:
     1. The file `com.accontrol.tornado.america.hisense_preferences.xml` contains an `access_token` string. Copy it aside.
     1. The file `com.aylanetworks.aylasdk.aylacache.xml` contains a very long encrypted string called lanconfig.
     1. Run in `python3` (I should really make this into a script):
        ```lang:python
        >>> import hashlib
        >>> import base64
        >>> from Crypto.Cipher import AES
        >>> lanconfig_enc = base64.b64decode("Base64 string copied from com.aylanetworks.aylasdk.aylacache.xml.")
        >>> access_token = "Access token string copeid from com.accontrol.tornado.america.hisense_preferences.xml."
        >>> auth_token = "auth_token " + access_token
        >>> key_data = auth_token.encode('utf-8')
        >>> key = hashlib.sha256(key_data).digest()
        >>> digest = hashlib.sha256()
        >>> digest.update(key_data)
        >>> digest.update(b"lanconfig-iv-salt")
        >>> iv = digest.digest()
        >>> cipher = AES.new(key[:AES.block_size], AES.MODE_CBC, iv[:AES.block_size])
        >>> lanconfig = cipher.decrypt(lanconfig_enc)
        >>> lanconfig[:-ord(lanconfig[len(lanconfig)-1:])]
        b'{"auto_sync":1,"keep_alive":30,"lanip_key":"XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\\u003d\\u003d","lanip_key_id":8888,"status":"enable"}'
        >>> "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX\u003d\u003d"
        'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX=='
        ```
     1. Write down you lanip_key and lanip_key_id. Use them to create a config.json file for the script.
