"""
The single source of truth for which Madire Cloud platform this build of
the app talks to — mirrors endpoint_agent/deployment_config.py exactly.
One platform instance serves every client; only the registration CODE is
client/device-specific, never the URL, so an end user never types one.

>>> BEFORE CUTTING A PRODUCTION RELEASE, CHANGE THIS <<<
Dev/LAN builds point at this machine's LAN IP; production builds must
point at the real public platform domain before that build ever reaches a
client. The mechanism is identical in every environment — only this
literal value changes per build.
"""
PLATFORM_URL = "http://192.168.8.167:8001/api/v1"
