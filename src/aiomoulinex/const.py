"""Identifiers of the SEB cloud services."""

SF_BASE = "https://account.groupeseb.com"
SF_CLIENT_ID = "3MVG99qusVZJwhskrTckKb5cJcadZj.gO2CSs265Vz0qQMGWsSZSBM5cTZ2nP9GXu04o7iOcczoNdXtJ5VWSZ"  # noqa: E501
SF_REDIRECT_URI = "gsmoduser://tokens"

SEB_API_BASE = "https://sebplatform.api.groupe-seb.com"
SEB_IOT_BASE = "https://iot.api.groupe-seb.com"
SEB_API_KEY = "GtPU4am4rpf83Zptq4xahtJsEytbrvKP"
# Key of the app of each brand: it gives every kind of appliance, in all its markets.
SEB_API_KEYS = {
    "moulinex": "tLQ6PasWAb4didcWJT6ufYybl4IJh54p",
    "tefal": "d8f3706f1d5c65ac2a44c9b8ab4ad67e",
    "krups": "460d1bd232b95f5aa04a60b07bdca353",
    "tfal": "3cd7c7653c09ff0496ab94284b31cd5b",
}
COOKEO_DOMAIN = "PRO_COO"
COMPANION_DOMAIN = "PRO_COM"

AWS_REGION = "eu-west-1"
COGNITO_URL = f"https://cognito-identity.{AWS_REGION}.amazonaws.com/"
COGNITO_IDENTITY_POOL = "eu-west-1:f25c4598-48da-4fda-9c1a-27fe30bd2ffe"
COGNITO_PROVIDER = "account.groupeseb.com"
MQTT_ENDPOINT = "a1p8u39dc9ign8-ats.iot.eu-west-1.amazonaws.com"
MQTT_PORT = 443
MQTT_KEEPALIVE = 30

USER_AGENT = "cookeat-android/22.0.0"

DEFAULT_TIMEOUT = 15.0
INITIAL_STATE_TIMEOUT = 3.0
# An appliance that turns on is said to be connected about 3.5 seconds before
# it answers a request for its status: ask again 4, 10 and 20 seconds later.
STATUS_RETRY_DELAYS = (4.0, 6.0, 10.0)
CREDENTIALS_RENEWAL_MARGIN = 300
RECONNECT_MIN_DELAY = 5.0
RECONNECT_MAX_DELAY = 300.0
