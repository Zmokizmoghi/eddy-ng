#!/usr/bin/env python3
# Temperature Profile Extension for probe_eddy_ng.py
# Implements temperature profiles for BTT Eddy NG
# Version: 1.0.0

import json
import logging
import math

class EddyTemperatureProfiles:
    """
    Temp Profiles manager
    Uses JSON to store profiles in printer.cfg
    TODO: Move to different config sections
    """

    def __init__(self, probe_eddy):
        self.probe = probe_eddy
        self.printer = probe_eddy._printer
        self.gcode = probe_eddy._gcode
        self.name = probe_eddy._full_name

        self.logger = logging.getLogger(__name__)

        self.temperature_sensor = None
        self._init_temperature_sensor()

        self.profiles = {}
        self.active_profile = None
        self.auto_switch_enabled = False
        self.temperature_tolerance = 2.0

        self._load_profiles()

        self._register_commands()

    def _init_temperature_sensor(self):
        try:
            # Probe name: probe_eddy_ng btt_eddy -> work with name part
            sensor_name = f"temperature_sensor {self.name.split()[-1]}"
            self.temperature_sensor = self.printer.lookup_object(sensor_name)
            self.logger.info(f"Found temperature sensor: {sensor_name}")
        except:
            try:
                self.temperature_sensor = self.printer.lookup_object("temperature_sensor btt_eddy")
                self.logger.info("Found temperature sensor: temperature_sensor btt_eddy")
            except:
                try:
                    self.temperature_sensor = self.printer.lookup_object("temperature_sensor btt_eddy_mcu")
                    self.logger.info("Using MCU temperature as fallback: temperature_sensor btt_eddy_mcu")
                except:
                    self.logger.warning("Temperature sensor not found, using fallback")

    def get_current_temperature(self):
        if self.temperature_sensor:
            try:
                reactor = self.printer.get_reactor()
                eventtime = reactor.monotonic()
                status = self.temperature_sensor.get_status(eventtime)
                temp = status.get('temperature', 25.0)
                return temp
            except Exception as e:
                self.logger.error(f"Error reading temperature: {e}")

        try:
            if hasattr(self.probe, '_sensor') and hasattr(self.probe._sensor, 'get_status'):
                status = self.probe._sensor.get_status(0)
                return status.get('temperature', 25.0)
        except:
            pass

        return 25.0 # TODO: raise an error here

    def _load_profiles(self):
        """Load temp profiles from printer config"""
        try:
            config = self.printer.lookup_object('configfile')
            sections = config.get_status(None)
            section_name = self.name

            if 'save_config_pending_items' in sections:
                saved_config = sections['save_config_pending_items']
                if section_name in saved_config:
                    section = saved_config[section_name]
                    profiles_json = section.get('temperature_profiles', '{}')
                    if profiles_json:
                        try:
                            self.profiles = json.loads(profiles_json)
                            self.logger.info(f"Loaded {len(self.profiles)} temperature profiles from saved config")
                        except json.JSONDecodeError as e:
                            self.logger.error(f"Failed to parse temperature profiles: {e}")
                            self.profiles = {}

                    self.auto_switch_enabled = section.get('temp_profiles_auto_switch', 'False') == 'True'
                    self.active_profile = section.get('temp_profiles_active', None)
                    self.temperature_tolerance = float(section.get('temp_profiles_tolerance', '2.0'))
                    return

            if hasattr(config, 'config') and section_name in config.config:
                section = config.config[section_name]
                profiles_json = section.get('temperature_profiles', '{}')
                if profiles_json:
                    try:
                        self.profiles = json.loads(profiles_json)
                        self.logger.info(f"Loaded {len(self.profiles)} temperature profiles")
                    except:
                        self.profiles = {}

                self.auto_switch_enabled = section.getboolean('temp_profiles_auto_switch', False)
                self.active_profile = section.get('temp_profiles_active', None)
                self.temperature_tolerance = section.getfloat('temp_profiles_tolerance', 2.0)
            else:
                self.logger.info("No saved profiles found")
        except Exception as e:
            self.logger.error(f"Error loading profiles: {e}")
            self.profiles = {}

    def _save_profiles(self):
        """Saves temp profiles to printer config"""
        configfile = self.printer.lookup_object('configfile')

        profiles_json = json.dumps(self.profiles, indent=2)
        configfile.set(self.name, 'temperature_profiles', profiles_json)

        configfile.set(self.name, 'temp_profiles_auto_switch', str(self.auto_switch_enabled))
        if self.active_profile:
            configfile.set(self.name, 'temp_profiles_active', self.active_profile)
        configfile.set(self.name, 'temp_profiles_tolerance', str(self.temperature_tolerance))

        self.gcode.respond_info("Temperature profiles saved. Run SAVE_CONFIG to persist changes.")

    def _register_commands(self):
        self.gcode.register_command(
            'EDDY_TEMP_PROFILE',
            self.cmd_EDDY_TEMP_PROFILE,
            desc="Manage BTT Eddy temperature profiles"
        )

    def select_profile_by_temperature(self, temp=None):
        """Select apropriate profile based on coil temp"""
        if temp is None:
            temp = self.get_current_temperature()

        best_profile = None
        min_distance = float('inf')

        for name, profile in self.profiles.items():
            if profile['temp_min'] <= temp <= profile['temp_max']:
                center = (profile['temp_min'] + profile['temp_max']) / 2
                distance = abs(temp - center)
                if distance < min_distance:
                    min_distance = distance
                    best_profile = name

        if best_profile and best_profile != self.active_profile:
            self.activate_profile(best_profile)
            self.logger.info(f"Auto-selected profile '{best_profile}' for {temp:.1f}°C")

        return best_profile

    def activate_profile(self, profile_name):
        if profile_name not in self.profiles:
            self.logger.error(f"Profile '{profile_name}' not found")
            return False

        profile = self.profiles[profile_name]
        self.active_profile = profile_name

        if 'calibration' in profile and profile['calibration']:
            self._apply_calibration(profile['calibration'])

        if 'tap_adjust_z' in profile:
            self.probe._tap_adjust_z = profile['tap_adjust_z']

        if 'reg_drive_current' in profile:
            self.probe._reg_drive_current = profile['reg_drive_current']
        if 'tap_drive_current' in profile:
            self.probe._tap_drive_current = profile['tap_drive_current']

        self.logger.info(f"Activated profile '{profile_name}'")
        return True

    def _apply_calibration(self, calibration_data):
        try:
            if 'frequency_map' in calibration_data:
                pass

            if 'z_offset' in calibration_data:
                self.probe.z_offset = calibration_data['z_offset']

        except Exception as e:
            self.logger.error(f"Failed to apply calibration: {e}")

    def save_current_calibration(self, profile_name):
        if profile_name not in self.profiles:
            self.logger.error(f"Profile '{profile_name}' not found")
            return False

        profile = self.profiles[profile_name]

        profile['calibration'] = {
            'z_offset': getattr(self.probe, 'z_offset', 0.0),
            'timestamp': self.printer.get_reactor().monotonic()
        }

        profile['tap_adjust_z'] = getattr(self.probe, '_tap_adjust_z', 0.0)

        profile['reg_drive_current'] = getattr(self.probe, '_reg_drive_current', 16)
        profile['tap_drive_current'] = getattr(self.probe, '_tap_drive_current', 16)

        profile['calibrated_at_temp'] = self.get_current_temperature()

        if hasattr(self.probe, '_dc_to_fmap'):
            profile['has_frequency_map'] = True

        self._save_profiles()
        self.logger.info(f"Saved calibration to profile '{profile_name}'")
        return True

    def cmd_EDDY_TEMP_PROFILE(self, gcmd):
        action = gcmd.get('ACTION', 'LIST').upper()

        if action == 'LIST':
            self._cmd_list_profiles(gcmd)
        elif action == 'CREATE':
            self._cmd_create_profile(gcmd)
        elif action == 'DELETE':
            self._cmd_delete_profile(gcmd)
        elif action == 'SELECT':
            self._cmd_select_profile(gcmd)
        elif action == 'SAVE':
            self._cmd_save_calibration(gcmd)
        elif action == 'AUTO':
            self._cmd_auto_switch(gcmd)
        elif action == 'STATUS':
            self._cmd_status(gcmd)
        else:
            raise gcmd.error(f"Unknown action: {action}")

    def _cmd_list_profiles(self, gcmd):
        current_temp = self.get_current_temperature()
        gcmd.respond_info(f"Current BTT Eddy temperature: {current_temp:.1f}°C")
        gcmd.respond_info(f"Active profile: {self.active_profile or 'None'}")
        gcmd.respond_info(f"Auto-switch: {'Enabled' if self.auto_switch_enabled else 'Disabled'}")

        if not self.profiles:
            gcmd.respond_info("No temperature profiles configured")
            return

        gcmd.respond_info("Available profiles:")
        for name, profile in self.profiles.items():
            status = []
            if name == self.active_profile:
                status.append("ACTIVE")
            if profile['temp_min'] <= current_temp <= profile['temp_max']:
                status.append("IN RANGE")

            calibrated_temp = profile.get('calibrated_at_temp', 'N/A')
            if calibrated_temp != 'N/A':
                calibrated_temp = f"{calibrated_temp:.1f}°C"

            status_str = f" [{', '.join(status)}]" if status else ""
            gcmd.respond_info(
                f"  {name}: {profile['temp_min']:.1f}-{profile['temp_max']:.1f}°C "
                f"(calibrated at {calibrated_temp}){status_str}"
            )

    def _cmd_create_profile(self, gcmd):
        name = gcmd.get('NAME')

        current_temp = self.get_current_temperature()
        temp_min = gcmd.get_float('MIN', None)
        temp_max = gcmd.get_float('MAX', None)

        if temp_min is None or temp_max is None:
            range_half = gcmd.get_float('RANGE', 5.0)
            temp_min = current_temp - range_half
            temp_max = current_temp + range_half

        if temp_min >= temp_max:
            raise gcmd.error(f"Invalid temperature range: {temp_min}-{temp_max}")

        self.profiles[name] = {
            'temp_min': temp_min,
            'temp_max': temp_max,
            'calibration': {},
            'tap_adjust_z': 0.0,
            'reg_drive_current': 15, # TODO: Fetch currents from calibarion data
            'tap_drive_current': 16,
            'calibrated_at_temp': None
        }

        self.active_profile = name
        self._save_profiles()

        gcmd.respond_info(
            f"Created profile '{name}' for {temp_min:.1f}-{temp_max:.1f}°C "
            f"(current temp: {current_temp:.1f}°C)"
        )

    def _cmd_delete_profile(self, gcmd):
        name = gcmd.get('NAME')

        if name not in self.profiles:
            raise gcmd.error(f"Profile '{name}' not found")

        del self.profiles[name]
        if self.active_profile == name:
            self.active_profile = None

        self._save_profiles()
        gcmd.respond_info(f"Deleted profile '{name}'")

    def _cmd_select_profile(self, gcmd):
        name = gcmd.get('NAME', None)

        if name == 'AUTO':
            selected = self.select_profile_by_temperature()
            if selected:
                gcmd.respond_info(f"Auto-selected profile '{selected}'")
            else:
                gcmd.respond_info("No suitable profile found for current temperature")
        elif name:
            if name not in self.profiles:
                raise gcmd.error(f"Profile '{name}' not found")
            self.activate_profile(name)
            gcmd.respond_info(f"Activated profile '{name}'")
        else:
            raise gcmd.error("NAME parameter required")

    def _cmd_save_calibration(self, gcmd):
        if not self.active_profile:
            raise gcmd.error("No active profile. Select a profile first.")

        current_temp = self.get_current_temperature()
        profile = self.profiles[self.active_profile]

        if not (profile['temp_min'] - self.temperature_tolerance <= current_temp <=
                profile['temp_max'] + self.temperature_tolerance):
            gcmd.respond_info(
                f"WARNING: Current temperature {current_temp:.1f}°C is outside "
                f"the profile range {profile['temp_min']:.1f}-{profile['temp_max']:.1f}°C"
            )
            if not gcmd.get_int('FORCE', 0):
                raise gcmd.error("Use FORCE=1 to save anyway")

        self.save_current_calibration(self.active_profile)
        gcmd.respond_info(
            f"Saved calibration to profile '{self.active_profile}' "
            f"at {current_temp:.1f}°C"
        )

    def _cmd_auto_switch(self, gcmd):
        enable = gcmd.get_int('ENABLE', None)

        if enable is None:
            self.auto_switch_enabled = not self.auto_switch_enabled
        else:
            self.auto_switch_enabled = bool(enable)

        self._save_profiles()
        gcmd.respond_info(
            f"Automatic profile switching {'enabled' if self.auto_switch_enabled else 'disabled'}"
        )

    def _cmd_status(self, gcmd):
        current_temp = self.get_current_temperature()

        gcmd.respond_info("=== BTT Eddy Temperature Profile Status ===")
        gcmd.respond_info(f"Sensor temperature: {current_temp:.2f}°C")
        gcmd.respond_info(f"Active profile: {self.active_profile or 'None'}")
        gcmd.respond_info(f"Auto-switching: {'Enabled' if self.auto_switch_enabled else 'Disabled'}")
        gcmd.respond_info(f"Temperature tolerance: ±{self.temperature_tolerance:.1f}°C")
        gcmd.respond_info(f"Total profiles: {len(self.profiles)}")

        if self.active_profile and self.active_profile in self.profiles:
            profile = self.profiles[self.active_profile]
            gcmd.respond_info(f"Profile range: {profile['temp_min']:.1f}-{profile['temp_max']:.1f}°C")
            if 'calibrated_at_temp' in profile and profile['calibrated_at_temp']:
                gcmd.respond_info(f"Calibrated at: {profile['calibrated_at_temp']:.1f}°C")
            gcmd.respond_info(f"TAP adjust Z: {profile.get('tap_adjust_z', 0.0):.3f}")


def add_temperature_profiles(probe_eddy_instance):
    """
    Add temp profiles support for existing ProbeEddy class

    Usage:
    At the end of __init__.py method in ProbeEddy class:
    from probe_eddy_ng_temp_profiles import add_temperature_profiles
    add_temperature_profiles(probe)
    """
    probe_eddy_instance.temp_profiles = EddyTemperatureProfiles(probe_eddy_instance)

    original_home_start = probe_eddy_instance.home_start if hasattr(probe_eddy_instance, 'home_start') else None

    def patched_home_start(*args, **kwargs):
        if probe_eddy_instance.temp_profiles.auto_switch_enabled:
            probe_eddy_instance.temp_profiles.select_profile_by_temperature()

        if original_home_start:
            return original_home_start(*args, **kwargs)

    if original_home_start:
        probe_eddy_instance.home_start = patched_home_start

    return probe_eddy_instance.temp_profiles
