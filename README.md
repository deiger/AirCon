# HiSense Air Conditioners

This program implements the LAN API for HiSense WiFi Air Conditioner module, model AEH-W4B1.
The program may fit other HiSense modules as well, but I have not tried any.
The module is installed in ACs that are either manufactured or only branded by many other companies. These include Beko, Westinghouse, Winia, Tornado, York and more.
It is not affiliated with either HiSense, any of it's subsidiaries, or any of its resellers.

## Prerequisites

1. Air Conditioner with HiSense AEH-W4B1 installed.
1. Configure the AC with the dedicated app: Log into the app, associate the AC and connect it to the network, as described in the app documentation.
1. Once everything has been configured, the AC can be blocked from connecting to the internet, as it will no longer be needed. Set it a static IP address in the router, and write it down.
1. Download and run [query_cli.py](query_cli.py), to fetch the LAN keys that will allow connecting to the AC. Pass it your login credentials, as well as the app ID from the list below:

   | Brand        | app ID     |
   |--------------|------------|
   | Beko         | beko-eu    |
   | Hisense (EU) | oem-eu     |
   | Hisense (US) | oem-us     |
   | Neutral (EU) | mid-eu     |
   | Neutral (US) | mid-us     |
   | Tornado      | tornado-us |
   | Westinghouse | wwh-us     |
   | Winia        | winia-us   |
   | York         | york-us    |

   For example:
   ```bash
   ./query_cli.py --user foo@example.com --passwd my_pass --app tornado-us --config config.json
   ```
   The CLI will generate a config file, that needs to be passed to the AC control server below.
   If you have more than one AC that you would like to control, create a separate config file for each AC, and run a separate control process. You can select the AC that the config is generated for by setting the `--device` flag to the device name you configured in the app.

## Run the AC control server

1. Download [hisense.py](hisense.py).
1. Test out that you can run the server, e.g.:
   ```bash
   ./hisense.py --port 8888 --ip 10.0.0.40 --config config.json --mqtt_host localhost
   ```
   Parameters:
   - `--port` or `-p` - Port for the web server.
   - `--ip` - The IP address for the AC.
   - `--config` - The config file with the credentials to connect to the AC.
   - `--mqtt_host` - The MQTT broker hostname or IP address. Must be set to enable MQTT.
   - `--mqtt_port` - The MQTT broker port. Default is 1883.
   - `--mqtt_client_id` - The MQTT client ID. If not set, a random client ID will be generated.
   - `--mqtt_user` - &lt;user:password&gt; for the MQTT channel. If not set, no authentication is used.
   - `--mqtt_topic` - The MQTT root topic. Default is &quot;hisense_ac&quot;. The server will listen on topic
     &lt;{mqtt_topic}/command&gt; and publish to &lt;{mqtt_topic}/status&gt;.
   - `--log_level` - The minimal log level to send to syslog. Default is WARNING.
