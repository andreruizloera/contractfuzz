"""contractfuzz: find undocumented assumptions in API clients.

Generates payloads that are valid according to an OpenAPI 3 contract but
unusual enough to break clients that assume more than the contract promises.
"""

__version__ = "0.1.0"
