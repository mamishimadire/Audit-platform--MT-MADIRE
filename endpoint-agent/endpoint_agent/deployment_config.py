"""
The single source of truth for which Madire Cloud platform this build of
the app talks to. There is exactly one platform instance serving every
client organization (the registration CODE is what's client/device-
specific, not this URL) — so baking it in means an end user only ever
enters a registration code, never a URL, on every install, in every
environment.

>>> BEFORE CUTTING A PRODUCTION RELEASE, CHANGE THIS <<<
Dev/LAN builds point at this machine's LAN IP; production builds must
point at the real public platform domain (e.g. https://app.madire.io/api/v1)
before that build is ever handed to a client. The mechanism (no URL entry,
just a code) is identical in every environment — only this literal value
changes per build.
"""
PLATFORM_URL = "http://192.168.8.167:8001/api/v1"
