# Electronics Diagnostic Expansion Pack — Summary

Generated: `2026-11-26T04:25:39+00:00`

Pack version: `1.5.1`

Schema version: `1.0.0`

## Counts

- **ic_records**: 35
- **mosfet_records**: 5
- **discrete_semiconductor_records**: 20
- **analog_ic_records**: 16
- **component_records_total**: 76
- **replacement_mappings**: 9
- **rail_families**: 25
- **diagnostic_rules**: 53
- **desktop_power_on_required_stages**: 14
- **desktop_optional_peripheral_checks**: 3
- **repair_cases**: 0
- **repair_case_seed_examples**: 1

## IC records by category

- battery_management_ic: 1
- bios_spi_flash: 2
- buck_controller: 6
- charger_ic: 4
- ec_controller: 2
- ldo_regulator: 5
- load_switch: 1
- pmic: 1
- pwm_controller: 9
- usb_pd_controller: 2
- voltage_regulator: 2

## MOSFET records by channel

- N-channel: 3
- P-channel: 2

## Diagnostic rules by topology

- always_on_standby: 10
- boost_converter: 5
- buck_converter: 21
- charger_input: 3
- ldo: 5
- load_switch: 1
- multi_phase_vrm: 8

## Rail families

- +1.8V
- +3VALW
- +5VALW
- +VCCRTC
- ADAPTER_INPUT
- ATX_12V
- ATX_3V3
- ATX_5V
- ATX_5VSB
- BOOST_GENERIC
- CPU_VRM_INPUT
- DDR_VDD
- DDR_VTT
- EPS_12V
- GPU_CORE
- LCD_VDD
- PCH_VCC_DESKTOP
- PCIE_12V
- RAM_VRM_DESKTOP
- RAM_VTT_DESKTOP
- USB_VBUS
- VCCSA
- VCORE
- VCORE_DESKTOP
- VRAM

## Validation

- **validate_database**: PASS (exit code 0)
- **validate_rules**: PASS (exit code 0)
