from aiohttp import web
import base64
from Crypto.Cipher import AES
from http import HTTPStatus
import json
import math
from logging import getLogger
import queue
import random
import string
import time
from typing import Callable

from .config import Config, Encryption
from .aircon import BaseDevice
from .error import Error, KeyIdReplaced

_LOGGER = getLogger(__name__)


class QueryHandlers:
    def __init__(self, devices: [BaseDevice]):
        self._devices_map = {}
        for device in devices:
            self._devices_map[device.ip_address] = device

    async def key_exchange_handler(self, request: web.Request) -> web.Response:
        """Handles a key exchange.
        Accepts the AC's random and time and pass its own.
        Note that a key encryption component is the lanip_key, mapped to the
        lanip_key_id provided by the AC. This secret part is provided by HiSense
        server. Fortunately the lanip_key_id (and lanip_key) are static for a given
        AC.
        """
        device = self._devices_map.get(request.remote)
        if not device:
            raise web.HTTPNotFound()
        updated_keys = {}
        post_data = await request.text()
        data = json.loads(post_data)
        try:
            key = data["key_exchange"]
            if key["ver"] != 1 or key["proto"] != 1 or key.get("sec"):
                _LOGGER.error(
                    "[Key_exchange][%s] Invalid key exchange: %s",
                    device.ip_address,
                    data,
                )
                raise web.HTTPBadRequest()
            updated_keys = device.update_key(key)
        except KeyIdReplaced as e:
            _LOGGER.error(
                "[Key_exchange][%s] %s\n%s", device.ip_address, e.title, e.message
            )
            raise web.HTTPNotFound()
        _LOGGER.debug("[Key_exchange][%s] Sending updated keys", device.ip_address)
        return web.json_response(updated_keys)

    async def command_handler(self, request: web.Request) -> web.Response:
        """Handles a command request.
        Request arrives from the AC. takes a command from the queue,
        builds the JSON, encrypts and signs it, and sends it to the AC.
        """
        command = {}
        device = self._devices_map.get(request.remote)
        if not device:
            _LOGGER.debug("[Command][%s] Device not registered", device.ip_address)
            raise web.HTTPNotFound()
        command["seq_no"] = device.get_command_seq_no()
        try:
            command["data"], property_updater = device.commands_queue.get_nowait()
        except queue.Empty:
            command["data"], property_updater = {}, None
        if property_updater:
            property_updater()  # TODO: should be async as well?

        if (
            "cmds" in command["data"].keys()
            and "property.json" in command["data"]["cmds"][0]["cmd"]["resource"]
        ):
            property_name = command["data"]["cmds"][0]["cmd"]["resource"].split("=")[-1]
            _LOGGER.debug(
                "[Command][%s] Sending GET_PROPERTY named '%s'",
                device.ip_address,
                property_name,
            )
        elif "properties" in command.keys():
            _LOGGER.debug(
                "[Command][%s] Sending SET_PROPERTY command for '%s', value '%s'",
                device.ip_address,
                command["properties"][0]["property"]["name"],
                command["properties"][0]["property"]["value"],
            )
        else:
            _LOGGER.debug(
                "[Command][%s] Sending command '%s'", device.ip_address, command
            )
        return web.json_response(self._encrypt_and_sign(device, command))

    async def property_update_handler(self, request: web.Request) -> web.Response:
        """Handles a property update request.
        Decrypts, validates, and pushes the value into the local properties store.
        """
        device = self._devices_map.get(request.remote)
        if not device:
            _LOGGER.debug(
                "[Property_update][%s] Device not registered", device.ip_address
            )
            raise web.HTTPNotFound()
        post_data = await request.text()
        data = json.loads(post_data)
        try:
            update = self._decrypt_and_validate(device, data)
        except Error:
            _LOGGER.exception(
                "[Property_update][%s] Failed to parse property update.",
                device.ip_address,
            )
            raise web.HTTPBadRequest()
        response = web.Response()
        if not device.is_update_valid(update["seq_no"]):
            _LOGGER.debug(
                "[Property_update][%s] Invalid seq number received.", device.ip_address
            )
            return response
        try:
            if not update["data"]:
                _LOGGER.debug(
                    "[Property_update][%s] Requested property is not supported.",
                    device.ip_address,
                )
                return response
            name = update["data"]["name"]
            data_type = device.get_property_type(name)
            value = data_type(update["data"]["value"])
            device.update_property(name, value)
            _LOGGER.debug(
                "[Property_update][%s] Property '%s' set to '%s'",
                device.ip_address,
                name,
                value,
            )
        except Exception as ex:
            _LOGGER.error(
                "[Property_update][%s] Failed to handle %s. Exception = %s",
                device.ip_address,
                update,
                ex,
            )
            # TODO: Should return internal error?
        return response

    async def get_status_handler(self, request: web.Request) -> web.Response:
        """Handles get status request (by a smart home hub).
        Returns the current internally stored state of the AC.
        """
        devices = []
        for device in self._devices_map.values():
            if (
                "device_ip" in request.query.keys()
                and device.ip_address != request.query["device_ip"]
            ):
                continue
            devices.append(
                {
                    "ip": device.ip_address,
                    "props": device.get_all_properties().to_dict(),
                }
            )
        return web.json_response({"devices": devices})

    async def queue_command_handler(self, request: web.Request) -> web.Response:
        """Handles queue command request (by a smart home hub)."""
        device = self._devices_map.get(request.query.get("device_ip"))
        if not device:
            raise web.HTTPBadRequest()
        try:
            device.queue_command(request.query["property"], request.query["value"])
        except:
            _LOGGER.exception("[Queue_command] Failed to queue command.")
            raise web.HTTPBadRequest()
        return web.json_response({"queued_commands": device.commands_queue.qsize()})

    def _encrypt_and_sign(self, device: BaseDevice, data: dict) -> dict:
        text = json.dumps(data)
        text = text.encode("utf-8")
        encryption = device.get_app_encryption()
        return {
            "enc": base64.b64encode(encryption.cipher.encrypt(self.pad(text))).decode(
                "utf-8"
            ),
            "sign": base64.b64encode(
                Encryption.hmac_digest(encryption.sign_key, text)
            ).decode("utf-8"),
        }

    def _decrypt_and_validate(self, device: BaseDevice, data: dict) -> dict:
        encryption = device.get_dev_encryption()
        text = self.unpad(encryption.cipher.decrypt(base64.b64decode(data["enc"])))
        sign = base64.b64encode(
            Encryption.hmac_digest(encryption.sign_key, text)
        ).decode("utf-8")
        if sign != data["sign"]:
            raise Error("Invalid signature for %s!" % text.decode("utf-8"))
        return json.loads(text.decode("utf-8"))

    @staticmethod
    def pad(data: bytes):
        """Zero padding for AES data encryption (non standard)."""
        new_size = math.ceil(len(data) / AES.block_size) * AES.block_size
        return data.ljust(new_size, bytes([0]))

    @staticmethod
    def unpad(data: bytes):
        """Remove Zero padding for AES data encryption (non standard)."""
        return data.rstrip(bytes([0]))

