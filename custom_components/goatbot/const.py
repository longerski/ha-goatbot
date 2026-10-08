"""Constants for the Goatbot integration."""

DOMAIN = "goatbot"

APP_ID = "1802238967805726722"
IDP_BASE_URL = "https://idp.okiot.net"
API_BASE_URL = "https://api.eu.okiot.net"

DEFAULT_SCAN_INTERVAL = 60

CMD_QUERY_STATE = 34
CMD_SUBSCRIBE = 40
CMD_SET_RAIN_SENSOR = 9
CMD_SET_CUTTING_HEIGHT = 8
CMD_TASK_CONTROL = 17
CMD_RETURN_TO_DOCK = 14
CMD_STOP = 46

# Payload for CMD_TASK_CONTROL that starts mowing the whole lawn
# automatically, as captured from the official app's "Start" button.
TASK_CONTROL_START = {"cutting_mode": 0, "region": [], "isAuto": False}
# Payload for CMD_TASK_CONTROL that pauses an in-progress mow.
TASK_CONTROL_PAUSE = {"cutting_mode": 1}

CUTTING_HEIGHT_MIN = 20
CUTTING_HEIGHT_MAX = 70
CUTTING_HEIGHT_STEP = 5

# How often to call POST /maps/traceKeep to keep a mower publishing its
# live position to the MQTT trace topic (matches the official app's cadence).
TRACE_KEEPALIVE_INTERVAL = 20

# Rated blade service life, in operating hours. The Goatbot app's
# "Maintenance Reminder" / push notifications show REMAINING blade life as
# a percentage (falling toward 0, not a "worn" counter rising toward 100 -
# confirmed by a push notification reading "blades at 5%, cutting may
# slip, replace soon", which only makes sense as remaining life). The
# cloud API has no maintenance endpoint, so this is back-calculated from
# two app readings: 8 % at workTotalTime 220.3 h (2026-09-09) and 5 % at
# 227.5 h (2026-09-11) both solve to ~239.5 h - a tight, consistent fit.
# Adjust this if the HA figure drifts away from what the app shows.
BLADE_RATED_HOURS = 239.5
