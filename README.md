# HiSense Air Conditioners

This program implements the Ayla Networks LAN API to interact with HiSense WiFi Air Conditioner module, models AEH-W4B1 and AEH-W4E1, as well as Fujitsu FGLair.

As discussed [here](../../issues/1), the program doesn't seem to fit the AEH-W4A1 module, which relies on entirely different protocol (implemented by the apps [Hi-Smart Life](https://play.google.com/store/apps/details?id=com.qd.android.livehome), [AirConnect](https://play.google.com/store/apps/details?id=com.oem.android.airconnect), [Smart Cool](https://play.google.com/store/apps/details?id=com.oem.android.livehome), [AC WIFI](https://play.google.com/store/apps/details?id=com.oem.android.ecold) and [טורנדו WiFi](https://play.google.com/store/apps/details?id=com.oem.android.tornadowifi)). Please let me know if you have a different experience, or tried it with other modules.

The module is installed in A/Cs and humidifiers that are either manufactured or only branded by many other companies. These include Beko, Westinghouse, Winia, Tornado, York and more.

**This program is not affiliated with Ayla Networks, HiSense, Fujitsu, any of their subsidiaries, or any of their resellers.**

## Prerequisites

1. Air Conditioner with HiSense AEH-W4B1 or AEH-W4E1 installed.
1. Have Python 3.7 or above installed. If using Raspberry Pi, either upgrade to Raspbian Buster, or manually install it in Raspbian Stretch.
1. Configure the A/Cs with the dedicated app. Links to each app are available in the table below. Log into the app, associate each A/C and connect it to the network, as described in the app documentation.
1. Once everything has been configured, the A/Cs can be blocked from connecting to the internet, as it will no longer be needed. Set them static IP addresses in the router, and write them down.
   * Note: _To avoid the need for manual changes later, make sure the app is aware of the new IP addresses before disconnecting the A/Cs from the internet._
1. Find the code for your app, from the list below:

   | Code       | App Name            | App link
   |------------|---------------------|---------|
   | beko-eu    | Beko?               | |
   | haxxair    | HAXXAIR WIFI REMOTE | [![](https://lh3.googleusercontent.com/-9FX7-sYlE2xDwG9uymjPejV-P8nI_hQ9zN7QDu6OgyYILbjdg5o38nQTvAmFTPyiw=s50-rw)](https://play.google.com/store/apps/details?id=com.aylanetworks.accontrol.haxxair) |
   | denali-us  | Denali Aire         | [![](https://lh3.googleusercontent.com/8NYl3eNN7M_cXmvo4ywj9al5794Ci_dzGYxYZopHd96Z4yr1M12e8xzk9mkz5cMELQ=s50-rw)](https://play.google.com/store/apps/details?id=com.smart.internationalus.denaliaire) |
   | fglair-eu  | FGLair              | [![](https://lh3.googleusercontent.com/LcrpWfFdRi3GriCV3MqPhkKsxV-IkwFHxZHHDugC__iaO1HE-7UyKuQj-bEWyggo8DFP=s50-rw)](https://play.google.com/store/apps/details?id=com.fujitsu.fglair) |
   | field-us   | HiSmart Air         | [![](https://lh3.googleusercontent.com/9p4SUOklfccVzJdrbhHZW8MlmioF-YgfLWOQBtad2N_A5AWtcyNv7X-M3QT1e2Fdam00=s50-rw)](https://play.google.com/store/apps/details?id=com.aylanetworks.accontrol.hisense) |
   | hisense-eu | HiSmart Life        | [![](https://lh3.googleusercontent.com/AbCPfEScNDwgsKozku6jmItFPVq9WJCl30jZKlSDFDAtlAiC3WRZZ4MlWEEWR8ZxKA=s50-rw)](https://play.google.com/store/apps/details?id=com.hisense.hismartinternationalforandroid) |
   | hisense-us | HiSmart Home        | [![](https://lh3.googleusercontent.com/Qs9UJVhczWYk-ij7UiRWoCDi2pYIoOUYuU5pBwOKQSD_07KHyAnLGg-myF7U9a387w=s50-rw)](https://play.google.com/store/apps/details?id=com.hisense.hismartinternationalus) |
   | hismart-eu | Smart-Living        | [![](https://lh3.googleusercontent.com/k9p0RMiW_xax5FIU5tpwSZav1In7tu6szGQopRWhSyRd2dIr0_L0IWHPVLSHxbrWrA=s50-rw)](https://play.google.com/store/apps/details?id=com.smart.international2) |
   | hismart-us | AI-Home             | [![](https://lh3.googleusercontent.com/eUJicIOk50rP391IFs0Xw6306adghQuiQtaLgUkxImuP6bAdHvQjS1gbIKY75Bd2mkA=s50-rw)](https://play.google.com/store/apps/details?id=com.smart.internationalus) |
   | huihe-us   | SunHome             | [![](https://lh3.googleusercontent.com/3tI6Nbx4ZlphD_b5O7bW3XcMEKnFkViOKMS9-cL9K9OQVyGJRjRmKu67JU8_t_w93iZs=s50-rw)](https://play.google.com/store/apps/details?id=com.sunvalley.sunhome) |
   | mid-eu     | WiFi AC             | [![](https://lh3.googleusercontent.com/LWmnlcSnT2hYmdwB2vq5SoBuaawkS8eu0F6n9Tytowrftp7kflmUXRAt_uWg7C0Fgspn=s50-rw)](https://play.google.com/store/apps/details?id=com.accontrol.mid.europe.hisense) |
   | mid-us     | Smiling Air         | [![](https://lh3.googleusercontent.com/op7-cqkm6N3JinyViCONKKgIVeMWI4BGO4TP3atRheGKG_vzsufh1PmEa-v9b8OAEPI=s50-rw)](https://play.google.com/store/apps/details?id=com.accontrol.mid.america.hisense) |
   | oem-eu     | Hi-Smart AC         | [![](https://lh3.googleusercontent.com/-HdiS1L18OjviXxGY68fvuBO3I4J1XGEEPOIc0f8p268f0ZJYkADHVvOgzH2wttsBwnk=s50-rw)](https://play.google.com/store/apps/details?id=com.accontrol.europe.hisense) |
   | oem-us     | Hisense?            | |
   | tornado-us | &#x2067;טורנדו WIFI גרסה 2&#x2069; | [![](https://lh3.googleusercontent.com/M9kU7oYeZTU8hVLChdJQL4giJacgUT2yFw-pqNk8JR4kbqbvl9x8dT88BC0admZrrQ=s50-rw)](https://play.google.com/store/apps/details?id=com.accontrol.tornado.america.hisense) |
   | winia-us   | 위니아 에어컨 홈스마트        | [![](https://lh3.googleusercontent.com/IGIkHlnLbFxTFGOk_aql3sVGgL9DLOtc3Ti_oDhQLUT8_-8PGmXjVBcQnmgqWxitB_U=s50-rw)](https://play.google.com/store/apps/details?id=com.accontrol.winia.america.hisense) |
   | wwh-us     | Westinghouse?       | |
   | york-us    | YORK Smart          | [![](https://lh3.googleusercontent.com/udf-qe7lXPJ5d7pi96WC8ex20-DuzAvAfyYX1i9B0zyvKjj0TLqoWwZmju-M5y0dQwE=s50-rw)](https://play.google.com/store/apps/details?id=com.accontrol.york.america.hisense) |

## Run the A/C control server in docker

This is the preferred method.

1. Download the [`docker-compose.yaml`](docker-compose.yaml) and [`options.json`](options.json). Update all the relevant fields in `options.json`:
   - For every app (multiple apps are supported), set `username` and `password` to your app login credentials, and `code` to the app code from the list above.
     These will be used to discover you A/Cs and get their LAN keys, if there are no config files in the config directory (`/opt/hisense`).
   - Set `mqtt_host` to the [MQTT](http://en.wikipedia.org/wiki/Mqtt) broker server, use `localhost` if running on the same host.
     Leave blank if not using MQTT.
   - Set `mqtt_user` and `mqtt_pass` to the MQTT credentials. Leave null (or drop) if no authentication is used.
   - Set `port` to the port to be used by the web server.
   - Set `log_level` to your desired verbosity level.

1. Run:
   ```bash
   docker-compose up -d
   ```
1. Check the logs and verify that everything is in shape:
   ```bash
   journalctl CONTAINER_NAME=hisense_ac
   ```

1. Profit!  
   The A/Cs should now be auto-discovered by [HomeAssistant](https://www.home-assistant.io/) or [openHAB](https://www.openhab.org/)
   (using the [HomeAssistant MQTT Components Binding](https://www.openhab.org/addons/bindings/mqtt.homeassistant/)).
   SmartThings requires manual setup, using the [groovy file](devicetypes/deiger/hisense-air-conditioner.src/hisense-air-conditioner.groovy), see below.

## Run the A/C control server manually

Use this method if the docker setup above does not work for you.

1. Download and install aircon module:
   ```bash
   python3.7 setup.py install
   ```

1. Run discovery command to fetch the LAN keys that will allow connecting to the A/C. Pass it your login credentials, as well as the code for your app from the list below:

   For example:
   ```bash
   python3.7 -m aircon discovery tornado-us foo@example.com my_pass
   ```
   The CLI will generate a config file for each A/C, that needs to be passed to the A/C
   control server below. You can select the A/C that the config is generated for by
   setting the `--device` flag to the device name you configured in the app.

* Note: _To update the server from head, run `git pull` on the repository and
  run setup. You may also need to re-run discovery._

1. Test out that you can run the server, e.g.:
   ```bash
   python3.7 -m aircon run --port 8888 --config config.json --mqtt_host localhost
   ```
   Parameters:
   - `--port` or `-p` - Port for the web server.
   - `--config` - The config file with the credentials to connect to the A/C.
   - `--mqtt_host` - The MQTT broker hostname or IP address. Must be set to enable MQTT.
   - `--mqtt_port` - The MQTT broker port. Default is 1883.
   - `--mqtt_client_id` - The MQTT client ID. If not set, a random client ID will be generated.
   - `--mqtt_user` - &lt;user:password&gt; for the MQTT channel. If not set, no authentication is used.
   - `--mqtt_topic` - The MQTT root topic. Default is &quot;hisense_ac&quot;. The server will listen on topics
     &lt;{mqtt_topic}/{property_name}/command&gt; and publish to &lt;{mqtt_topic}/{property_name}/status&gt;.
   - `--log_level` - The minimal log level to send to syslog. Default is WARNING.
1. Access e.g. using curl:
   ```bash
   curl -ik 'http://localhost:8888/hisense/status'
   curl -ik 'http://localhost:8888/hisense/command?property=t_power&value=ON'
   ```

### Multiple Air Conditioners
In order to use with multiple Air Conditioners, simply add multiple --config params.
MQTT topic will contain your topic defined by flag --mqtt_topic (hisense_ac by default) and device MAC address (for uniqueness).

### Run as a service
Assuming your username is "pi"

1. Create a dedicated directory for the script files, and move the files to it.
   Pass the ownership to root. e.g.:
   ```bash
   sudo mkdir /opt/hisense
   sudo mv config*.json /opt/hisense
   sudo chown pi:pi /opt/hisense/*
   ```
1. Create a service configuration file (as root), e.g. `/lib/systemd/system/hisense.service`:
   ```INI
   [Unit]
   Description=Hisense A/C server
   After=network.target

   [Service]
   ExecStart=/usr/bin/python3.7 -m aircon run --port 8888 --config config.json --mqtt_host localhost
   WorkingDirectory=/opt/hisense
   StandardOutput=inherit
   StandardError=inherit
   Restart=always
   User=pi

   [Install]
   WantedBy=multi-user.target
   ```
1. Link to it from `/etc/systemd/system/`:
   ```bash
   sudo ln -s /lib/systemd/system/hisense.service /etc/systemd/system/multi-user.target.wants/hisense.service
   ```
1. Enable and start the new service:
   ```bash
   sudo systemctl enable hisense.service
   sudo systemctl start hisense.service
   ```
1. If you use [MQTT](http://en.wikipedia.org/wiki/Mqtt) for [HomeAssistant](https://www.home-assistant.io/) or
   [openHAB](https://www.openhab.org/), the broker should now provide the updated status of the A/C, and accepts commands.

## Available Properties

Listed here are the properties available through the API for standard A/Cs
(FGLair and humidifers have different properties):

| Property         | Read Only | Values                                 | Comment                                                                  |
|------------------|-----------|----------------------------------------|--------------------------------------------------------------------------|
| f_electricity    | x         | Integer                                |                                                                          |
| f_e_arkgrille    | x         | 0, 1                                   | Alarm from cabinet grille protection                                     |
| f_e_incoiltemp   | x         | 0, 1                                   | Indoor coil temperature sensor in fault                                  |
| f_e_incom        | x         | 0, 1                                   | Indoor and outdoor communication in fault                                |
| f_e_indisplay    | x         | 0, 1                                   | Communication faulty between indoor control panel and display panel      |
| f_e_ineeprom     | x         | 0, 1                                   | Error in EEPROM of indoor control panel                                  |
| f_e_inele        | x         | 0, 1                                   | Communication faulty between indoor control panel and indoor power panel |
| f_e_infanmotor   | x         | 0, 1                                   | Indoor fan motor operation abnormal                                      |
| f_e_inhumidity   | x         | 0, 1                                   | Indoor humidity sensor in fault                                          |
| f_e_inkeys       | x         | 0, 1                                   | Communication faulty between indoor control panel and keyboard plate     |
| f_e_inlow        | x         | 0, 1                                   |                                                                          |
| f_e_intemp       | x         | 0, 1                                   | Indoor temperature sensor in fault                                       |
| f_e_invzero      | x         | 0, 1                                   | Fault found from indoor voltage crossing zero detection                  |
| f_e_outcoiltemp  | x         | 0, 1                                   | The temperature sensor in outdoor coil faulty                            |
| f_e_outeeprom    | x         | 0, 1                                   | Outdoor EEPROM error                                                     |
| f_e_outgastemp   | x         | 0, 1                                   | Exhaust temperature sensor faulty                                        |
| f_e_outmachine2  | x         | 0, 1                                   |                                                                          |
| f_e_outmachine   | x         | 0, 1                                   |                                                                          |
| f_e_outtemp      | x         | 0, 1                                   | Outdoor ambient temperature sensor faulty                                |
| f_e_outtemplow   | x         | 0, 1                                   |                                                                          |
| f_e_push         | x         | 0, 1                                   | Communication faulty between WiFi control panel and indoor control panel |
| f_filterclean    | x         | 0, 1                                   | Does the filter require cleaning                                         |
| f_humidity       | x         | Integer                                | Relative humidity percent                                                |
| f_power_display  | x         | 0, 1                                   |                                                                          |
| f_temp_in        | x         | Decimal                                | Environment temperature in Fahrenheit                                    |
| f_voltage        | x         | Integer                                |                                                                          |
| t_backlight      |           | ON, OFF                                | Turn the display on/off                                                  |
| t_device_info    |           | 0, 1                                   |                                                                          |
| t_display_power  |           | 0, 1                                   |                                                                          |
| t_eco            |           | OFF, ON                                | Economy mode                                                             |
| t_fan_leftright  |           | OFF, ON                                | Horizontal air flow                                                      |
| t_fan_mute       |           | OFF, ON                                | Quite mode                                                               |
| t_fan_power      |           | OFF, ON                                | Vertical air flow                                                        |
| t_fan_speed      |           | AUTO, LOWER, LOW, MEDIUM, HIGH, HIGHER | Fan Speed                                                                |
| t_ftkt_start     |           | Integer                                |                                                                          |
| t_power          |           | OFF, ON                                | Power                                                                    |
| t_run_mode       |           | OFF, ON                                | Double frequency                                                         |
| t_setmulti_value |           | Integer                                |                                                                          |
| t_sleep          |           | STOP, ONE, TWO, THREE, FOUR            | Sleep mode                                                               |
| t_temp           |           | Integer                                | Temperature in Fahrenheit                                                |
| t_temptype       |           | CELSIUS, FAHRENHEIT                    | Displayed temperature unit                                               |
| t_temp_eight     |           | OFF, ON                                | Eight heat mode                                                          |
| t_temp_heatcold  |           | OFF, ON                                | Fast cool heat                                                           |
| t_work_mode      |           | FAN, HEAT, COOL, DRY, AUTO             | Work mode                                                                |

## SmartThings and HomeAssistant support
You will need a groovy script to enable SmartThings integration with the Air Conditioner, through the control server above.
It currently implements the main functionality (turn on/off, AC mode, fan speed, dimmer etc.).

The groovy file is available [here](devicetypes/deiger/hisense-air-conditioner.src/hisense-air-conditioner.groovy), for download and installation through the [Groovy IDE](https://graph.api.smartthings.com). As I'm continuously improving this script, it would be more efficient to use the IDE's github integration, in order to stay up-to-date.

HomeAsststant is now fully supported through [MQTT Discovery](https://www.home-assistant.io/docs/mqtt/discovery/). Properly configured devices are auto-configured and populated in the Lovelace dashboard.

## Code Contributions
Pull requests are always welcome.

Please use [YAPF](https://github.com/google/yapf) with the style config defined here to style your code.
Single quotes are used throughout the code-base. Unfortunately YAPF still doesn't support mandating this (support exists in the [fixers branch](https://github.com/google/yapf/tree/fixers)), so please be mindful.
