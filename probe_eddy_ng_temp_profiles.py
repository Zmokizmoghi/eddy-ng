#!/usr/bin/env python3
"""
Temperature Profile Extension for probe_eddy_ng.py
Adds temperature profile support for BTT Eddy
Version: 2.0.2
"""

import logging

try:
    from klippy.configfile import ConfigWrapper

    IS_KALICO = True
except ImportError:
    from configfile import ConfigWrapper

    IS_KALICO = False

class EddyTemperatureProfiles:
    """
    Temperature profile manager for BTT Eddy
    Stores profiles as separate config sections in printer.cfg
    """

    def __init__(self, config: ConfigWrapper, probe_eddy):
        self.probe = probe_eddy
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = probe_eddy._full_name
        self.config = config

        # Initialize logger first
        self.logger = logging.getLogger(__name__)

        # Get BTT Eddy temperature sensor object
        self.temperature_sensor = None
        self.printer.register_event_handler("klippy:ready", self._init_temperature_sensor)

        # Profiles and settings
        self.profiles = {}
        self.active_profile = None
        self.auto_switch_enabled = False
        self.temperature_tolerance = 2.0

        # Load profiles from config
        self._load_profiles()

        # Register commands
        self.printer.register_event_handler("klippy:ready", self._register_commands)

    def _init_temperature_sensor(self):
        """Initialize access to BTT Eddy temperature sensor"""
        try:
            # In your config: [temperature_sensor btt_eddy]
            sensor_name = f"temperature_sensor {self.name.split()[-1]}"
            self.temperature_sensor = self.printer.lookup_object(sensor_name)
            self.logger.info(f"Found temperature sensor: {sensor_name}")
        except:
            # Try alternative names
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
        """Get current BTT Eddy sensor temperature"""
        if self.temperature_sensor:
            try:
                reactor = self.printer.get_reactor()
                eventtime = reactor.monotonic()
                status = self.temperature_sensor.get_status(eventtime)
                temp = status.get('temperature', 25.0)
                return temp
            except Exception as e:
                self.logger.error(f"Error reading temperature: {e}")

        # Fallback - try to get through probe object itself
        try:
            if hasattr(self.probe, '_sensor') and hasattr(self.probe._sensor, 'get_status'):
                status = self.probe._sensor.get_status(0)
                return status.get('temperature', 25.0)
        except:
            pass

        return 25.0

    def _load_profiles(self):
        """Load profiles from configuration sections"""
        # Load main settings from probe section
        self.auto_switch_enabled = self.config.getboolean('temp_profiles_auto_switch', False)
        self.active_profile = self.config.get('temp_profiles_active', None)
        self.temperature_tolerance = self.config.getfloat('temp_profiles_tolerance', 2.0)

        # Load profile sections - use config object directly
        all_config = self.config.get_printer().lookup_object('configfile')

        # Get all section names from the main config
        for section_name in self.config.get_prefix_sections('eddy_temp_profile '):
            # Extract profile name from section name
            profile_name = section_name.get_name().split('eddy_temp_profile ')[1]
            # self.logger.info(f"Section name from `section_name`: {section_name.get_name()}")

            # # Get the section config
            # section = self.config.getsection(section_name)
            # self.logger.info(f"Section: {section}")
            # self.logger.info(f"Section name: {section.get_name()}")

            section = section_name

            profile = {
                'temp_min': section.getfloat('temp_min'),
                'temp_max': section.getfloat('temp_max'),
                'tap_adjust_z': section.getfloat('tap_adjust_z', 0.0),
                'reg_drive_current': section.getint('reg_drive_current', 15),
                'tap_drive_current': section.getint('tap_drive_current', 16),
                'calibration_version': section.getint('calibration_version', 5),
                'calibrated_at_temp': section.getfloat('calibrated_at_temp', None),
            }

            # Load calibrated_drive_currents
            dc_str = section.get('calibrated_drive_currents', None)
            if dc_str:
                profile['calibrated_drive_currents'] = dc_str

            # Load calibration data fields
            calibrations = {}
            # Get all options in the section
            for option in section.get_prefix_options('calibration_'):
                if not option.endswith('_version'):
                    calibrations[option] = section.get(option)

            if calibrations:
                profile['calibrations'] = calibrations

            self.profiles[profile_name] = profile

        if self.profiles:
            self.logger.info(f"Loaded {len(self.profiles)} temperature profiles from config")

    def _save_profile_to_config(self, profile_name):
        """Save a single profile to configuration"""
        if profile_name not in self.profiles:
            return False

        profile = self.profiles[profile_name]
        configfile = self.printer.lookup_object('configfile')

        section_name = f'eddy_temp_profile {profile_name}'

        # Save basic profile settings
        configfile.set(section_name, 'temp_min', '%.2f' % profile['temp_min'])
        configfile.set(section_name, 'temp_max', '%.2f' % profile['temp_max'])
        configfile.set(section_name, 'tap_adjust_z', '%.6f' % profile.get('tap_adjust_z', 0.0))
        configfile.set(section_name, 'reg_drive_current', str(profile.get('reg_drive_current', 15)))
        configfile.set(section_name, 'tap_drive_current', str(profile.get('tap_drive_current', 16)))
        configfile.set(section_name, 'calibration_version', str(profile.get('calibration_version', 5)))

        if profile.get('calibrated_at_temp') is not None:
            configfile.set(section_name, 'calibrated_at_temp', '%.2f' % profile['calibrated_at_temp'])

        # Save calibrated_drive_currents
        if 'calibrated_drive_currents' in profile:
            configfile.set(section_name, 'calibrated_drive_currents', profile['calibrated_drive_currents'])

        # Save calibration data
        if 'calibrations' in profile:
            for cal_key, cal_data in profile['calibrations'].items():
                configfile.set(section_name, cal_key, cal_data)

        return True

    def _save_settings(self):
        """Save global settings to configuration"""
        configfile = self.printer.lookup_object('configfile')

        # Save global settings to main probe section
        configfile.set(self.name, 'temp_profiles_auto_switch', str(self.auto_switch_enabled))
        if self.active_profile:
            configfile.set(self.name, 'temp_profiles_active', self.active_profile)
        configfile.set(self.name, 'temp_profiles_tolerance', '%.1f' % self.temperature_tolerance)

    def _delete_profile_from_config(self, profile_name):
        """Remove a profile section from configuration"""
        configfile = self.printer.lookup_object('configfile')
        section_name = f'eddy_temp_profile {profile_name}'

        # Mark section for deletion by setting a special marker
        # Klipper will remove the section when SAVE_CONFIG is run
        configfile.remove_section(section_name)

    def _register_commands(self):
        """Register G-code commands"""
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'EDDY_TEMP_PROFILE',
            self.cmd_EDDY_TEMP_PROFILE,
            desc="Manage BTT Eddy temperature profiles"
        )

    def select_profile_by_temperature(self, temp=None):
        """Automatically select appropriate profile by temperature"""
        if temp is None:
            temp = self.get_current_temperature()

        best_profile = None
        min_distance = float('inf')

        for name, profile in self.profiles.items():
            # Check if temperature is within profile range
            if profile['temp_min'] <= temp <= profile['temp_max']:
                # Find profile with center closest to current temperature
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
        """Activate specified profile"""
        if profile_name not in self.profiles:
            self.logger.error(f"Profile '{profile_name}' not found")
            return False

        profile = self.profiles[profile_name]
        self.active_profile = profile_name

        # Apply calibration_* data directly to probe
        if 'calibrations' in profile and hasattr(self.probe, '_saved_calibration'):
            if not self.probe._saved_calibration:
                self.probe._saved_calibration = {}

            for cal_key, cal_data in profile['calibrations'].items():
                self.probe._saved_calibration[cal_key] = cal_data
                self.logger.info(f"Loaded {cal_key}")

        # Apply drive currents
        if 'calibrated_drive_currents' in profile and hasattr(self.probe, '_calibrated_drive_currents'):
            dc_str = profile['calibrated_drive_currents']
            if ',' in dc_str:
                self.probe._calibrated_drive_currents = [int(x.strip()) for x in dc_str.split(',')]
            else:
                self.probe._calibrated_drive_currents = [int(dc_str)]

        if 'reg_drive_current' in profile and hasattr(self.probe, '_reg_drive_current'):
            self.probe._reg_drive_current = int(profile['reg_drive_current'])

        if 'tap_drive_current' in profile and hasattr(self.probe, '_tap_drive_current'):
            self.probe._tap_drive_current = int(profile['tap_drive_current'])

        # Apply tap_adjust_z
        if 'tap_adjust_z' in profile and hasattr(self.probe, '_tap_adjust_z'):
            self.probe._tap_adjust_z = float(profile['tap_adjust_z'])

        # Apply calibration_version
        if 'calibration_version' in profile and hasattr(self.probe, '_calibration_version'):
            self.probe._calibration_version = int(profile['calibration_version'])

        # Reload calibration in probe
        if hasattr(self.probe, '_load_calibration'):
            try:
                self.probe._load_calibration()
                self.logger.info(f"Reloaded calibration for profile '{profile_name}'")
            except Exception as e:
                self.logger.warning(f"Could not reload calibration: {e}")

        self.logger.info(f"Activated profile '{profile_name}' with {len(profile.get('calibrations', {}))} calibrations")
        return True

    def save_current_calibration(self, profile_name):
        """Save current calibration to specified profile"""
        if profile_name not in self.profiles:
            self.logger.error(f"Profile '{profile_name}' not found")
            return False

        profile = self.profiles[profile_name]

        # Get current configuration
        configfile = self.printer.lookup_object('configfile')

        # Read calibration directly from probe if available
        calibrations = {}

        # Try to get from probe's _saved_calibration
        if hasattr(self.probe, '_saved_calibration'):
            for key, value in self.probe._saved_calibration.items():
                if key.startswith('calibration_'):
                    calibrations[key] = value

        # If not found, try from save_config
        if not calibrations:
            status = configfile.get_status(None)
            if 'save_config_pending_items' in status and self.name in status['save_config_pending_items']:
                saved_config = status['save_config_pending_items'][self.name]
                for key, value in saved_config.items():
                    if key.startswith('calibration_') and not key.endswith('_version'):
                        calibrations[key] = value

        if calibrations:
            profile['calibrations'] = calibrations
            self.logger.info(f"Saved {len(calibrations)} calibration entries")

        # Save drive currents from probe
        if hasattr(self.probe, '_calibrated_drive_currents'):
            if isinstance(self.probe._calibrated_drive_currents, list):
                profile['calibrated_drive_currents'] = ','.join(map(str, self.probe._calibrated_drive_currents))
            else:
                profile['calibrated_drive_currents'] = str(self.probe._calibrated_drive_currents)

        profile['reg_drive_current'] = getattr(self.probe, '_reg_drive_current', 16)
        profile['tap_drive_current'] = getattr(self.probe, '_tap_drive_current', 16)
        profile['tap_adjust_z'] = getattr(self.probe, '_tap_adjust_z', 0.0)
        profile['calibration_version'] = getattr(self.probe, '_calibration_version', 5)

        # Save current temperature
        profile['calibrated_at_temp'] = self.get_current_temperature()

        # Save profile to config
        self._save_profile_to_config(profile_name)
        self._save_settings()

        self.logger.info(f"Saved calibration to profile '{profile_name}'")
        return True

    def cmd_EDDY_TEMP_PROFILE(self, gcmd):
        """G-code command for managing temperature profiles"""
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
        """List all profiles"""
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

            calibrated_temp = profile.get('calibrated_at_temp')
            if calibrated_temp is not None:
                calibrated_temp = f"{calibrated_temp:.1f}°C"
            else:
                calibrated_temp = "N/A"

            status_str = f" [{', '.join(status)}]" if status else ""
            gcmd.respond_info(
                f"  {name}: {profile['temp_min']:.1f}-{profile['temp_max']:.1f}°C "
                f"(calibrated at {calibrated_temp}){status_str}"
            )

    def _cmd_create_profile(self, gcmd):
        """Create new profile"""
        name = gcmd.get('NAME')

        # Get range parameters
        current_temp = self.get_current_temperature()
        temp_min = gcmd.get_float('MIN', None)
        temp_max = gcmd.get_float('MAX', None)

        # If bounds not specified, create range around current temperature
        if temp_min is None or temp_max is None:
            range_half = gcmd.get_float('RANGE', 5.0)
            temp_min = current_temp - range_half
            temp_max = current_temp + range_half

        # Check range validity
        if temp_min >= temp_max:
            raise gcmd.error(f"Invalid temperature range: {temp_min}-{temp_max}")

        # Create profile
        self.profiles[name] = {
            'temp_min': temp_min,
            'temp_max': temp_max,
            'tap_adjust_z': 0.0,
            'reg_drive_current': 15,
            'tap_drive_current': 16,
            'calibration_version': 5,
            'calibrated_at_temp': None
        }

        self.active_profile = name

        # Save to config
        self._save_profile_to_config(name)
        self._save_settings()

        gcmd.respond_info(
            f"Created profile '{name}' for {temp_min:.1f}-{temp_max:.1f}°C "
            f"(current temp: {current_temp:.1f}°C)"
        )
        gcmd.respond_info("Run SAVE_CONFIG to persist changes")

    def _cmd_delete_profile(self, gcmd):
        """Delete profile"""
        name = gcmd.get('NAME')

        if name not in self.profiles:
            raise gcmd.error(f"Profile '{name}' not found")

        # Delete from config
        self._delete_profile_from_config(name)

        # Delete from memory
        del self.profiles[name]
        if self.active_profile == name:
            self.active_profile = None

        self._save_settings()

        gcmd.respond_info(f"Deleted profile '{name}'")
        gcmd.respond_info("Run SAVE_CONFIG to persist changes")

    def _cmd_select_profile(self, gcmd):
        """Select active profile"""
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
            self._save_settings()
            gcmd.respond_info(f"Activated profile '{name}'")
            gcmd.respond_info("Run SAVE_CONFIG to make active profile persistent")
        else:
            raise gcmd.error("NAME parameter required")

    def _cmd_save_calibration(self, gcmd):
        """Save current calibration to active profile"""
        if not self.active_profile:
            raise gcmd.error("No active profile. Select a profile first.")

        current_temp = self.get_current_temperature()
        profile = self.profiles[self.active_profile]

        # Check if temperature is within acceptable range
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
        gcmd.respond_info("Run SAVE_CONFIG to persist calibration")

    def _cmd_auto_switch(self, gcmd):
        """Enable/disable automatic profile switching"""
        enable = gcmd.get_int('ENABLE', None)

        if enable is None:
            # Toggle state
            self.auto_switch_enabled = not self.auto_switch_enabled
        else:
            self.auto_switch_enabled = bool(enable)

        self._save_settings()
        gcmd.respond_info(
            f"Automatic profile switching {'enabled' if self.auto_switch_enabled else 'disabled'}"
        )
        gcmd.respond_info("Run SAVE_CONFIG to persist changes")

    def _cmd_status(self, gcmd):
        """Show current profile system status"""
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
            if profile.get('calibrated_at_temp') is not None:
                gcmd.respond_info(f"Calibrated at: {profile['calibrated_at_temp']:.1f}°C")
            gcmd.respond_info(f"TAP adjust Z: {profile.get('tap_adjust_z', 0.0):.3f}")

            # Show calibration data
            if 'calibrations' in profile:
                gcmd.respond_info(f"Stored calibrations: {list(profile['calibrations'].keys())}")

        # Show current values from probe
        if hasattr(self.probe, '_reg_drive_current'):
            gcmd.respond_info(f"Current reg_drive_current: {self.probe._reg_drive_current}")
        if hasattr(self.probe, '_tap_drive_current'):
            gcmd.respond_info(f"Current tap_drive_current: {self.probe._tap_drive_current}")
        if hasattr(self.probe, '_tap_adjust_z'):
            gcmd.respond_info(f"Current tap_adjust_z: {self.probe._tap_adjust_z:.3f}")
        if hasattr(self.probe, '_calibrated_drive_currents'):
            gcmd.respond_info(f"Current calibrated_drive_currents: {self.probe._calibrated_drive_currents}")


# Function for integration into __init__.py or probe_eddy_ng.py
def add_temperature_profiles(config, probe_eddy_instance):
    """
    Add temperature profile support to existing ProbeEddy instance

    Usage:
    In __init__.py after creating ProbeEddy:
    from probe_eddy_ng_temp_profiles import add_temperature_profiles
    add_temperature_profiles(config, probe)
    """
    probe_eddy_instance.temp_profiles = EddyTemperatureProfiles(config, probe_eddy_instance)

    # Patch homing method for auto-profile selection
    original_home_start = probe_eddy_instance.home_start if hasattr(probe_eddy_instance, 'home_start') else None

    def patched_home_start(*args, **kwargs):
        # Auto-select profile before homing if enabled
        if probe_eddy_instance.temp_profiles.auto_switch_enabled:
            probe_eddy_instance.temp_profiles.select_profile_by_temperature()

        # Call original method
        if original_home_start:
            return original_home_start(*args, **kwargs)

    if original_home_start:
        probe_eddy_instance.home_start = patched_home_start

    return probe_eddy_instance.temp_profiles
