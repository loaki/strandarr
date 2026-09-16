WEATHER_ARCHIVE = "weather_archive"
MARINE_ARCHIVE = "marine_archive"
WEATHER_FORECAST = "weather_forecast"
MARINE_FORECAST = "marine_forecast"

GFW_FISHING = "gfw_fishing"
AIS_LIVE = "ais_live"

GBIF = "gbif"
PELAGIS_HISTOCARTO = "pelagis_histocarto"

OBSERVED = (MARINE_ARCHIVE, WEATHER_ARCHIVE)
PREDICTED = (MARINE_FORECAST, WEATHER_FORECAST)

OBSERVED_BEFORE_PREDICTED = OBSERVED + PREDICTED


def precedence(source: str) -> int:
    order = OBSERVED_BEFORE_PREDICTED
    return order.index(source) if source in order else len(order)
