import aiohttp
from aiohttp import web
import argparse
import asyncio
import base64
from http import HTTPStatus
from http.client import HTTPConnection, InvalidURL
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import logging
import logging.handlers
import paho.mqtt.client as mqtt
import signal
import socket
import sys
import threading
import time
import _thread
from urllib.parse import parse_qs, urlparse, ParseResult

from .app_mappings import SECRET_MAP
from .config import Config
from .error import Error
from .aircon import BaseDevice, AcDevice, FglDevice, FglBDevice, HumidifierDevice
from .discovery import perform_discovery
from .notifier import Notifier
from .mqtt_client import MqttClient
from .query_handlers import QueryHandlers


async def query_status_worker(devices: [BaseDevice]):
    _STATUS_UPDATE_INTERVAL = 600.0
    _WAIT_FOR_EMPTY_QUEUE = 10.0
    while True:
        # In case the AC is stuck, and not fetching commands, avoid flooding
        # the queue with status updates.
        for device in devices:
            while device.commands_queue.qsize() > 10:
                await asyncio.sleep(_WAIT_FOR_EMPTY_QUEUE)
            device.queue_status()
        await asyncio.sleep(_STATUS_UPDATE_INTERVAL)


def ParseArguments() -> argparse.Namespace:
    """Parse command line arguments."""
    arg_parser = argparse.ArgumentParser(
        description="JSON server for HiSense air conditioners.", allow_abbrev=False
    )
    arg_parser.add_argument(
        "--log_level",
        default="WARNING",
        choices={"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"},
        help="Minimal log level.",
    )
    subparsers = arg_parser.add_subparsers(
        dest="cmd", help="Determines what server should do"
    )
    subparsers.required = True

    parser_run = subparsers.add_parser(
        "run", help="Runs the server to control the device"
    )
    parser_run.add_argument(
        "-p", "--port", required=True, type=int, help="Port for the server."
    )
    group_device = parser_run.add_argument_group(
        "Device", "Arguments that are related to the device"
    )
    group_device.add_argument(
        "--config", required=True, action="append", help="LAN Config file."
    )
    group_device.add_argument(
        "--type",
        required=True,
        action="append",
        choices={"ac", "fgl", "fgl_b", "humidifier"},
        help="Device type (for systems other than Hisense A/C).",
    )

    group_mqtt = parser_run.add_argument_group("MQTT", "Settings related to the MQTT")
    group_mqtt.add_argument(
        "--mqtt_host", default=None, help="MQTT broker hostname or IP address."
    )
    group_mqtt.add_argument(
        "--mqtt_port", type=int, default=1883, help="MQTT broker port."
    )
    group_mqtt.add_argument("--mqtt_client_id", default=None, help="MQTT client ID.")
    group_mqtt.add_argument(
        "--mqtt_user", default=None, help="<user:password> for the MQTT channel."
    )
    group_mqtt.add_argument("--mqtt_topic", default="hisense_ac", help="MQTT topic.")

    parser_discovery = subparsers.add_parser(
        "discovery", help="Runs the device discovery"
    )
    parser_discovery.add_argument(
        "app", choices=set(SECRET_MAP), help="The app used for the login."
    )
    parser_discovery.add_argument("user", help="Username for the app login.")
    parser_discovery.add_argument("passwd", help="Password for the app login.")
    parser_discovery.add_argument(
        "-d",
        "--device",
        default=None,
        help="Device name to fetch data for. If not set, takes all.",
    )
    parser_discovery.add_argument(
        "--prefix", required=False, default="config_", help="Config file prefix."
    )
    parser_discovery.add_argument(
        "--properties", action="store_true", help="Fetch the properties for the device."
    )
    return arg_parser.parse_args()


def setup_logger(log_level):
    if sys.platform == "linux":
        logging_handler = logging.handlers.SysLogHandler(address="/dev/log")
    elif sys.platform == "darwin":
        logging_handler = logging.handlers.SysLogHandler(address="/var/run/syslog")
    elif sys.platform.lower() in ["windows", "win32"]:
        logging_handler = logging.handlers.SysLogHandler()
    # else:  # Unknown platform, revert to stderr
    logging_handler = logging.StreamHandler(sys.stderr)
    logging_handler.setFormatter(
        logging.Formatter(
            fmt="{levelname[0]}{asctime}.{msecs:03.0f}  "
            "{filename}:{lineno}] {message}",
            datefmt="%m%d %H:%M:%S",
            style="{",
        )
    )
    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.addHandler(logging_handler)


async def setup_and_run_http_server(parsed_args, devices: [BaseDevice]):
    # TODO: Handle these if needed.
    # '/local_lan/node/conn_status.json': _query_handlers.connection_status_handler,
    # '/local_lan/connect_status': _query_handlers.module_request_handler,
    # '/local_lan/status.json': _query_handlers.setup_device_details_handler,
    # '/local_lan/wifi_scan.json': _query_handlers.module_request_handler,
    # '/local_lan/wifi_scan_results.json': _query_handlers.module_request_handler,
    # '/local_lan/wifi_status.json': _query_handlers.module_request_handler,
    # '/local_lan/regtoken.json': _query_handlers.module_request_handler,
    # '/local_lan/wifi_stop_ap.json': _query_handlers.module_request_handler
    query_handlers = QueryHandlers(devices)
    app = web.Application()
    app.add_routes(
        [
            web.get("/hisense/status", query_handlers.get_status_handler),
            web.get("/hisense/command", query_handlers.queue_command_handler),
            web.post(
                "/local_lan/key_exchange.json", query_handlers.key_exchange_handler
            ),
            web.get("/local_lan/commands.json", query_handlers.command_handler),
            web.post(
                "/local_lan/property/datapoint.json",
                query_handlers.property_update_handler,
            ),
            web.post(
                "/local_lan/property/datapoint/ack.json",
                query_handlers.property_update_handler,
            ),
            web.post(
                "/local_lan/node/property/datapoint.json",
                query_handlers.property_update_handler,
            ),
            web.post(
                "/local_lan/node/property/datapoint/ack.json",
                query_handlers.property_update_handler,
            ),
        ]
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=parsed_args.port)
    await site.start()


async def mqtt_loop(mqtt_client: MqttClient):
    _MQTT_LOOP_TIMEOUT = 1
    while True:
        mqtt_client.loop()
        await asyncio.sleep(_MQTT_LOOP_TIMEOUT)


async def run(parsed_args):
    if len(parsed_args.type) != len(parsed_args.config):
        raise ValueError("Each device has to have specified type and config file")

    notifier = Notifier(parsed_args.port)
    devices = []
    for i in range(len(parsed_args.config)):
        with open(parsed_args.config[i], "rb") as f:
            data = json.load(f)
        name = data["name"]
        ip = data["lan_ip"]
        lanip_key = data["lanip_key"]
        lanip_key_id = data["lanip_key_id"]
        if parsed_args.type[i] == "ac":
            device = AcDevice(name, ip, lanip_key, lanip_key_id, notifier.notify)
        elif parsed_args.type[i] == "fgl":
            device = FglDevice(name, ip, lanip_key, lanip_key_id, notifier.notify)
        elif parsed_args.type[i] == "fgl_b":
            device = FglBDevice(name, ip, lanip_key, lanip_key_id, notifier.notify)
        elif parsed_args.type[i] == "humidifier":
            device = HumidifierDevice(
                name, ip, lanip_key, lanip_key_id, notifier.notify
            )
        else:
            logging.error("Unknown type of device: %s", parsed_args.type[i])
            sys.exit(1)  # Should never get here.
        notifier.register_device(device)
        devices.append(device)

    mqtt_client = None
    if parsed_args.mqtt_host:
        mqtt_topics = {
            "pub": "/".join((parsed_args.mqtt_topic, "{}", "{}", "status")),
            "sub": "/".join((parsed_args.mqtt_topic, "{}", "{}", "command")),
        }
        mqtt_client = MqttClient(parsed_args.mqtt_client_id, mqtt_topics, devices)
        if parsed_args.mqtt_user:
            mqtt_client.username_pw_set(*parsed_args.mqtt_user.split(":", 1))
        mqtt_client.connect(parsed_args.mqtt_host, parsed_args.mqtt_port)
        for device in devices:
            device.add_property_change_listener(mqtt_client.mqtt_publish_update)

    async with aiohttp.ClientSession(conn_timeout=5.0) as session:
        await asyncio.gather(
            mqtt_loop(mqtt_client),
            setup_and_run_http_server(parsed_args, devices),
            query_status_worker(devices),
            notifier.start(session),
        )


def _escape_name(name: str):
    safe_name = name.replace(" ", "_").lower()
    return "".join(x for x in safe_name if x.isalnum())


async def discovery(parsed_args):
    async with aiohttp.ClientSession(conn_timeout=5.0) as session:
        try:
            all_configs = await perform_discovery(
                session,
                parsed_args.app,
                parsed_args.user,
                parsed_args.passwd,
                parsed_args.device,
                parsed_args.properties,
            )
        except:
            print("Error occurred.")
            sys.exit(1)

    for config in all_configs:
        properties_text = ""
        if "properties" in config.keys():
            properties_text = "Properties:\n{}".format(
                json.dumps(config["properties"], indent=2)
            )
        print(
            "Device {} has:\nIP address: {}\nlanip_key: {}\nlanip_key_id: {}\n{}\n".format(
                config["product_name"],
                config["lan_ip"],
                config["lanip_key"],
                config["lanip_key_id"],
                properties_text,
            )
        )

        file_content = {
            "name": config["product_name"],
            "lan_ip": config["lan_ip"],
            "lanip_key": config["lanip_key"],
            "lanip_key_id": config["lanip_key_id"],
        }
        with open(
            parsed_args.prefix + _escape_name(config["product_name"]) + ".json", "w"
        ) as f:
            f.write(json.dumps(file_content))


if __name__ == "__main__":
    parsed_args = ParseArguments()  # type: argparse.Namespace
    setup_logger(parsed_args.log_level)

    if parsed_args.cmd == "run":
        asyncio.run(run(parsed_args))
    elif parsed_args.cmd == "discovery":
        asyncio.run(discovery(parsed_args))
