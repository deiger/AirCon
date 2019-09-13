/**
 *  Hisense Air Conditioner
 *
 *  Copyright 2019 Dror Eiger
 *
 *  Licensed under the GNU General Public License, Version 3.0 (the "License"); you may not use this file except
 *  in compliance with the License. You may obtain a copy of the License at:
 *
 *      https://www.gnu.org/licenses/gpl-3.0.en.html
 *
 *  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed
 *  on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License
 *  for the specific language governing permissions and limitations under the License.
 *
 */

preferences {
  input("host", "text", title: "IP Address", description: "The IP address and port for the Hisense server.")
}

metadata {
  definition(name: "Hisense Air Conditioner", namespace: "deiger", author: "Dror Eiger", mnmn: "SmartThings", ocfDeviceType: "oic.d.airconditioner", vid: "SmartThings-smartthings-Hisense_Air_Conditioner") {
    capability "Air Conditioner Mode"
    capability "Fan Speed"
    capability "Filter Status"
    capability "Health Check"
    capability "Power Meter"
    capability "Rapid Cooling"
    capability "Relative Humidity Measurement"
    capability "Switch"
    capability "Temperature Measurement"
    capability "Thermostat Setpoint"
    capability "Voltage Measurement"

    command "setAirConditionerMode"
    command "setFanSpeed"
    command "setRapidCooling"
    command "off"
    command "on"
    command "nextFanSpeed"
    command "nextAirConditionerMode"
    command "temperatureUp"
    command "temperatureDown"
    command "displayOff"
    command "displayOn"

    attribute "airConditionerMode", "ENUM"
    attribute "fanSpeed", "NUMBER"
    attribute "filterStatus", "ENUM"
    attribute "power", "NUMBER"
    attribute "rapidCooling", "ENUM"
    attribute "humidity", "NUMBER"
    attribute "switch", "ENUM"
    attribute "temperature", "NUMBER"
    attribute "thermostatSetpoint", "NUMBER"
    attribute "voltage", "NUMBER"
    attribute "display", "ENUM"
    attribute "temperatureUnit", "string"
  }

  simulator {
  }

  tiles(scale: 2) {
    multiAttributeTile(name:"temperature", type:"thermostat", width: 6, height: 4) {
      tileAttribute("device.switch", key: "PRIMARY_CONTROL") {
        attributeState("off", label: '${name}', action: "on", backgroundColor: "#ffffff", nextState:"on", icon:"st.thermostat.ac.air-conditioning")
        attributeState("on", label: '${name}', action: "off", backgroundColor: "#79b821", nextState:"off", icon:"st.thermostat.ac.air-conditioning")
        attributeState("offline", label:'${name}', backgroundColor:"#bc2323", defaultState: true, icon:"st.thermostat.ac.air-conditioning")
      }
      tileAttribute("device.thermostatSetpoint", key: "VALUE_CONTROL") {
        attributeState("VALUE_UP", action: "temperatureUp")
        attributeState("VALUE_DOWN", action: "temperatureDown")
      }
      tileAttribute("device.temperature", key: "SECONDARY_CONTROL") {
        attributeState("temp", label:'${currentValue}', unit:"dC", icon: "st.alarm.temperature.normal")
      }
    }
    standardTile("airConditionerMode", "device.airConditionerMode", width: 2, height: 2, decoration: "flat") {
      state("fanOnly", label:'Fan', action: "nextAirConditionerMode", backgroundColor:"#145D78", nextState:"heat", icon: "st.thermostat.fan-on")
      state("heat", label:'Heat', action: "nextAirConditionerMode", backgroundColor:"#e86d13", nextState:"cool", icon: "st.thermostat.heat")
      state("cool", label:'Cool', action: "nextAirConditionerMode", backgroundColor:"#00a0dc", nextState:"dry", icon: "st.thermostat.cool")
      state("dry", label:'Dry', action: "nextAirConditionerMode", backgroundColor:"#44B621", nextState:"auto", icon: "st.vents.wet")
      state("auto", label:'Auto', action: "nextAirConditionerMode", backgroundColor: "#ffffff", nextState:"fanOnly", icon: "st.thermostat.auto")
    }
    standardTile("fanSpeed", "device.fanSpeed", width: 2, height: 2, decoration: "flat") {
      state("0", label: 'Auto', action: "nextFanSpeed", nextState:"5", icon:"st.thermostat.fan-auto")
      state("5", label: 'Lower', action: "nextFanSpeed", nextState:"6", icon:"st.thermostat.fan-on")
      state("6", label: 'Low', action: "nextFanSpeed", nextState:"7", icon:"st.thermostat.fan-on")
      state("7", label: 'Medium', action: "nextFanSpeed", nextState:"8", icon:"st.thermostat.fan-on")
      state("8", label: 'High', action: "nextFanSpeed", nextState:"9", icon:"st.thermostat.fan-on")
      state("9", label: 'Higher', action: "nextFanSpeed", nextState:"0", icon:"st.thermostat.fan-on")
      state("-1", label:'Not Supported')
    }
    standardTile("display", "device.display", width: 2, height: 2, decoration: "flat") {
      state("on", label: 'Disaply On', action: "displayOff", backgroundColor: "#79b821", nextState:"off", icon: "st.switches.light.on")
      state("off", label: 'Display Off', action: "displayOn", backgroundColor: "#ffffff", nextState:"on", icon: "st.switches.light.off")
    }
    standardTile("rapidCooling", "device.rapidCooling", width: 2, height: 2, decoration: "flat") {
      state("on", label: 'Rapid On', action: "setRapidCooling 'off'", backgroundColor: "#79b821", nextState:"off", icon: "st.vents.vent-open")
      state("off", label: 'Rapid Off', action: "setRapidCooling 'on'", backgroundColor: "#ffffff", nextState:"on", icon: "st.vents.vent-closed")
    }
    valueTile("humidity", "device.humidity", width: 2, height: 2, decoration: "flat") {
      state("humidity", label: '${currentValue}%', backgroundColor: "#ffffff")
    }
    main("temperature")
    details([
      "temperature", "airConditionerMode", "fanSpeed", "display", "rapidCooling", "humidity"
    ])
  }
}

def installed() {
  initialize()
}

def updated() {
  initialize()
}

def initialize() {
  unschedule(updateStatus)
  runEvery1Minute(updateStatus)
}

void temperatureUp() {
  updateTemperature(state.thermostatSetpoint + 1)
}

void temperatureDown() {
  updateTemperature(state.thermostatSetpoint - 1)
}

void updateTemperature(float new_temp) {
  // Since the AC actually works only in F, convert and round in F and then convert back.
  def tempF = convertTempToF(new_temp).round().toInteger()
  sendCommand("t_temp", tempF)
  state.thermostatSetpoint = convertTempFromF(tempF)
  updateField("thermostatSetpoint", state.thermostatSetpoint.round().toInteger(), state.temperatureUnit)
}

void nextAirConditionerMode() {
  switch (state.airConditionerMode) {
    case "fanOnly":
      setAirConditionerMode("HEAT")
      break
    case "heat":
      setAirConditionerMode("COOL")
      break
    case "cool":
      setAirConditionerMode("DRY")
      break
    case "dry":
      setAirConditionerMode("AUTO")
      break
    case "auto":
      setAirConditionerMode("FAN")
      break
    default:
      log.debug "Invalid state.airConditionerMode ${state.airConditionerMode}"
  }
}

void setAirConditionerMode(String mode) {
  sendCommand("t_work_mode", mode)
  updateStateAirConditionerMode(mode)
  updateField("airConditionerMode", state.airConditionerMode)
}

void updateStateAirConditionerMode(String mode) {
  switch (mode) {
    case "FAN":
      state.airConditionerMode = "fanOnly"
      break
    case "HEAT":
      state.airConditionerMode = "heat"
      break
    case "COOL":
      state.airConditionerMode = "cool"
      break
    case "DRY":
      state.airConditionerMode = "dry"
      break
    case "AUTO":
      state.airConditionerMode = "auto"
      break
    default:
      state.airConditionerMode = "notSupported"
      break
  }
}

void nextFanSpeed() {
  switch (state.fanSpeed) {
    case 0:
      setFanSpeed("LOWER")
      break
    case 5:
      setFanSpeed("LOW")
      break
    case 6:
      setFanSpeed("MEDIUM")
      break
    case 7:
      setFanSpeed("HIGH")
      break
    case 8:
      setFanSpeed("HIGHER")
      break
    case 9:
      setFanSpeed("AUTO")
      break
    default:
      log.debug "Invalid state.fanSpeed ${state.fanSpeed}"
  }
}

void setFanSpeed(String speed) {
  sendCommand("t_fan_speed", speed)
  updateStateFanSpeed(speed)
  updateField("fanSpeed", state.fanSpeed)
}

void updateStateFanSpeed(String speed) {
  switch (speed) {
    case "AUTO":
      state.fanSpeed = 0
      break
    case "LOWER":
      state.fanSpeed = 5
      break
    case "LOW":
      state.fanSpeed = 6
      break
    case "MEDIUM":
      state.fanSpeed = 7
      break
    case "HIGH":
      state.fanSpeed = 8
      break
    case "HIGHER":
      state.fanSpeed = 9
      break
    default:
      state.fanSpeed = -1
      break
  }
}

void setRapidCooling(String status) {
  sendCommand("t_temp_heatcold", status == "on" ? "ON" : "OFF")
  state.rapidCooling = status
  updateField("rapidCooling", state.rapidCooling)
}

void off() {
  sendCommand("t_power", "OFF")
  state.switch = "off"
  updateField("switch", state.switch)
}

void on() {
  sendCommand("t_power", "ON")
  state.switch = "on"
  updateField("switch", state.switch)
}

void displayOff() {
  sendCommand("t_backlight", "ON")
  state.display = "off"
  updateField("display", state.display)
}

void displayOn() {
  sendCommand("t_backlight", "OFF")
  state.display = "on"
  updateField("display", state.display)
}

def sendCommand(String property, value) {
  String valueStr = value.toString()
  sendQuery("/hisense/command?property=" + property + "&value=" + valueStr, null)
}

def updateStatus() {
  sendQuery("/hisense/status", updateStatusHandler)
}

void updateField(String field, value, String unit="") {
  String valueStr = value.toString()
  def oldValue = device.currentState(field)?.stringValue
  if (valueStr != oldValue) {
    sendEvent(name: field, value: valueStr, unit: unit, descriptionText: "${field} is ${value}${unit}", displayed: true)
  }
}

void updateStatusHandler(physicalgraph.device.HubResponse hubResponse) {
  log.debug "updateStatusHandler(${hubResponse.body})"
  def status = hubResponse?.json
  if (status) {
    state.switch = status.t_power == "ON" ? "on" : "off"
    state.display = status.t_backlight == "ON" ? "off" : "on"
    state.rapidCooling = status.t_temp_heatcold == "ON" ? "on" : "off"
    updateStateAirConditionerMode(status.t_work_mode)
    updateStateFanSpeed(status.t_fan_speed)
    state.temperatureUnit = status.t_temptype == "CELSIUS" ? "C" : "F"
    state.thermostatSetpoint = convertTempFromF(status.t_temp)
    state.temperature = convertTempFromF(status.f_temp_in)
    if (status.f_humidity > 0) {
      state.humidity = status.f_humidity
    }
    log.debug "Current state: ${state}"
    updateField("switch", state.switch)
    updateField("display", state.display)
    updateField("rapidCooling", state.rapidCooling)
    updateField("airConditionerMode", state.airConditionerMode)
    updateField("fanSpeed", state.fanSpeed)
    updateField("thermostatSetpoint", state.thermostatSetpoint.round().toInteger(), state.temperatureUnit)
    updateField("temperature", state.temperature.round().toInteger(), state.temperatureUnit)
  }
}

float convertTempFromF(float temp) {
  if (state.temperatureUnit == "F") {
    return temp
  }
  return ((temp - 32) / 1.8).toFloat()
}

float convertTempToF(float temp) {
  if (state.temperatureUnit == "F") {
    return temp
  }
  return (temp * 1.8 + 32).toFloat()
}

def sendQuery(path, _callback) {
  def options = [
    "method": "GET",
    "path": path,
    "headers": [
      "HOST": settings.host,
      "Content-Type": "application/json",
    ]
  ]
  log.debug options
  def hubAction = new physicalgraph.device.HubAction(options, null, [callback: _callback])
  sendHubCommand(hubAction)
}
