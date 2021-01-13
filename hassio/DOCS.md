# Home Assistant Add-on: HiSense Air Conditioners

## Prerequisites

1. Air Conditioner with HiSense AEH-W4B1 or AEH-W4E1 WiFi module installed.
   These include A/Cs by multiple brands, including Fujitsu, Beko, Westinghouse,
   Winia, Tornado, York and more.
1. An [MQTT broker](https://www.home-assistant.io/docs/mqtt/broker/) installed,
   whether it is Mosquitto or the default Home Assistant MQTT broker. Please
   make sure to install and set up that add-on before continuing.

# Configuration

1. Find your application code from the list
   [here](https://github.com/deiger/AirCon#prerequisites).
1. Set the configuration as follows:
   ```yaml
   app:
     - username: App user name
       password: App password
       code: App code
   log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL. Default is INFO.
   mqtt_host: IP address (or localhost).
   mqtt_user: User name for MQTT server. Remove if no authentication is used.
   mqtt_pass: Password for MQTT server. Remove if no authentication is used.
   port: Port number for the web server.
   ```
   * Note: _If multiple apps are used, add them as separate values under `app`_
