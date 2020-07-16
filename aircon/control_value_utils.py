from .properties import (AcWorkMode, AirFlow, Economy, FanSpeed, 
    FastColdHeat, Quiet, Power, TemperatureUnit)

def get_fan_speed_value(control: int) -> FanSpeed:
  int_val = (control >> 1) & 15
  return FanSpeed(int_val)

def set_fan_speed_value(control: int, value: FanSpeed) -> None:
  int_val = value.value
  return (control & ~31) | ((int_val << 1) | 1)

def get_power_value(control: int) -> Power:
  int_val = (control >> 6) & 1
  return Power(int_val)

def set_power_value(control: int, value: Power) -> None:
  int_val = value.value
  return (control & ~(3 << 5)) | (((int_val << 1) | 1) << 5)

def get_work_mode_value(control: int) -> AcWorkMode:
  int_val = (control >> 9) & 7
  return AcWorkMode(int_val)

def set_work_mode_value(control: int, value: AcWorkMode) -> None:
  int_val = value.value
  return (control & ~(15 << 8)) | (((int_val << 1) | 1) << 8)

def get_heat_cold_value(control: int) -> FastColdHeat:
  int_val = (control >> 13) & 1
  return FastColdHeat(int_val)

def set_heat_cold_value(control: int, value: FastColdHeat) -> None:
  int_val = value.value
  return (control & ~(3 << 12)) | (((int_val << 1) | 1) << 12)

def get_eco_value(control: int) -> Economy:
  int_val = (control >> 15) & 1
  return Economy(int_val)

def set_eco_value(control: int, value: Economy) -> None:
  int_val = value.value
  return (control & ~(3 << 14)) | (((int_val << 1) | 1) << 14)

def get_temp_value(control: int) -> int:
  return (control >> 17) & 63

def set_temp_value(control: int, value: int) -> None:
  return (control & ~(127 << 16)) | (((value << 1) | 1) << 16)

def get_fan_power_value(control: int) -> AirFlow:
  int_val = (control >> 25) & 1
  return AirFlow(int_val)

def set_fan_power_value(control: int, value: AirFlow) -> None:
  int_val = value.value
  return (control & ~(3 << 24)) | (((int_val << 1) | 1) << 24)
 
def get_fan_lr_value(control: int) -> AirFlow:
  int_val = (control >> 27) & 1
  return AirFlow(int_val)

def set_fan_lr_value(control: int, value: AirFlow) -> None:
  int_val = value.value
  return (control & ~(3 << 26)) | (((int_val << 1) | 1) << 26)

def get_fan_mute_value(control: int) -> Quiet:
  int_val = (control >> 29) & 1
  return Quiet(int_val)

def set_fan_mute_value(control: int, value: Quiet) -> None:
  int_val = value.value
  return (control & ~(3 << 28)) | (((int_val << 1) | 1) << 28) 

def get_temptype_value(control: int) -> TemperatureUnit:
  int_val = (control >> 31) & 1 
  return TemperatureUnit(int_val)

def set_temptype_value(control: int, value: TemperatureUnit) -> None:
  int_val = value.value
  return (control & ~(3 << 30)) | (((int_val << 1) | 1) << 30)