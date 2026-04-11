#!/bin/bash
# Collect SMART metrics from all disks for node_exporter textfile collector.
# Outputs Prometheus exposition format to stdout.
# Runs as root (via cron) because smartctl needs root privileges.
set -eu

smartctl=/usr/sbin/smartctl

# Discover all block devices that support SMART
parse_smartctl_attributes() {
  local disk="$1"
  local disk_label
  disk_label=$(basename "$disk")
  local type="$2"

  # Get SMART health status
  local health
  health=$($smartctl -H "$disk" -d "$type" 2>/dev/null | grep -i "SMART overall-health" | awk -F': ' '{print $2}' | tr -d ' ')
  if [ "$health" = "PASSED" ]; then
    echo "smartmon_device_smart_healthy{disk=\"${disk_label}\",type=\"${type}\"} 1"
  elif [ -n "$health" ]; then
    echo "smartmon_device_smart_healthy{disk=\"${disk_label}\",type=\"${type}\"} 0"
  fi

  # Get SMART attributes (ATA drives)
  $smartctl -A "$disk" -d "$type" 2>/dev/null | \
  grep -E "^[[:space:]]*[0-9]+" | \
  while read -r id attribute flag value worst thresh type_ updated when_failed raw_value; do
    # Normalize attribute name: lowercase, replace spaces/hyphens with underscores
    attr_name=$(echo "$attribute" | tr '[:upper:]' '[:lower:]' | tr '-' '_')
    echo "smartmon_attr_raw_value{disk=\"${disk_label}\",type=\"${type}\",smart_id=\"${id}\",name=\"${attr_name}\"} ${raw_value}"
    echo "smartmon_attr_value{disk=\"${disk_label}\",type=\"${type}\",smart_id=\"${id}\",name=\"${attr_name}\"} ${value}"
    echo "smartmon_attr_worst{disk=\"${disk_label}\",type=\"${type}\",smart_id=\"${id}\",name=\"${attr_name}\"} ${worst}"
    echo "smartmon_attr_threshold{disk=\"${disk_label}\",type=\"${type}\",smart_id=\"${id}\",name=\"${attr_name}\"} ${thresh}"
  done

  # Get device info (temperature, power-on hours from SMART info section)
  local temp
  temp=$($smartctl -A "$disk" -d "$type" 2>/dev/null | grep -i "Temperature_Celsius" | awk '{print $10}')
  if [ -n "$temp" ]; then
    echo "smartmon_temperature_celsius{disk=\"${disk_label}\",type=\"${type}\"} ${temp}"
  fi
}

# Also handle NVMe drives
parse_nvme_attributes() {
  local disk="$1"
  local disk_label
  disk_label=$(basename "$disk")

  local health
  health=$($smartctl -H "$disk" 2>/dev/null | grep -i "SMART overall-health" | awk -F': ' '{print $2}' | tr -d ' ')
  if [ "$health" = "PASSED" ]; then
    echo "smartmon_device_smart_healthy{disk=\"${disk_label}\",type=\"nvme\"} 1"
  elif [ -n "$health" ]; then
    echo "smartmon_device_smart_healthy{disk=\"${disk_label}\",type=\"nvme\"} 0"
  fi

  $smartctl -A "$disk" 2>/dev/null | while IFS=: read -r key val; do
    val=$(echo "$val" | tr -d ' ' | tr -d ',')
    key_clean=$(echo "$key" | tr -d ' ' | tr '[:upper:]' '[:lower:]' | tr '-' '_' | tr ' ' '_')
    case "$key" in
      *"Temperature"*)         echo "smartmon_nvme_temperature_celsius{disk=\"${disk_label}\"} ${val%C*}" ;;
      *"Percentage Used"*)     echo "smartmon_nvme_percentage_used{disk=\"${disk_label}\"} ${val%%%*}" ;;
      *"Power On Hours"*)      echo "smartmon_nvme_power_on_hours{disk=\"${disk_label}\"} ${val}" ;;
      *"Power Cycles"*)        echo "smartmon_nvme_power_cycles{disk=\"${disk_label}\"} ${val}" ;;
      *"Media and Data Integrity Errors"*) echo "smartmon_nvme_media_errors{disk=\"${disk_label}\"} ${val}" ;;
      *"Critical Warning"*)    echo "smartmon_nvme_critical_warning{disk=\"${disk_label}\"} ${val}" ;;
      *"Unsafe Shutdowns"*)    echo "smartmon_nvme_unsafe_shutdowns{disk=\"${disk_label}\"} ${val}" ;;
    esac
  done
}

echo "# HELP smartmon_device_smart_healthy SMART health status (1=PASSED, 0=FAILED)"
echo "# TYPE smartmon_device_smart_healthy gauge"
echo "# HELP smartmon_attr_raw_value SMART attribute raw value"
echo "# TYPE smartmon_attr_raw_value gauge"
echo "# HELP smartmon_attr_value SMART attribute normalized value"
echo "# TYPE smartmon_attr_value gauge"
echo "# HELP smartmon_attr_worst SMART attribute worst value"
echo "# TYPE smartmon_attr_worst gauge"
echo "# HELP smartmon_attr_threshold SMART attribute threshold"
echo "# TYPE smartmon_attr_threshold gauge"
echo "# HELP smartmon_temperature_celsius Drive temperature in Celsius"
echo "# TYPE smartmon_temperature_celsius gauge"
echo "# HELP smartmon_nvme_temperature_celsius NVMe drive temperature in Celsius"
echo "# TYPE smartmon_nvme_temperature_celsius gauge"
echo "# HELP smartmon_nvme_percentage_used NVMe percentage used"
echo "# TYPE smartmon_nvme_percentage_used gauge"
echo "# HELP smartmon_nvme_power_on_hours NVMe power on hours"
echo "# TYPE smartmon_nvme_power_on_hours gauge"
echo "# HELP smartmon_nvme_media_errors NVMe media and data integrity errors"
echo "# TYPE smartmon_nvme_media_errors gauge"
echo "# HELP smartmon_nvme_critical_warning NVMe critical warning"
echo "# TYPE smartmon_nvme_critical_warning gauge"

# Scan for SMART-capable devices
$smartctl --scan 2>/dev/null | while read -r disk type_flag type rest; do
  if [ "$type_flag" = "-d" ]; then
    case "$type" in
      nvme*)
        parse_nvme_attributes "$disk"
        ;;
      *)
        parse_smartctl_attributes "$disk" "$type"
        ;;
    esac
  fi
done
