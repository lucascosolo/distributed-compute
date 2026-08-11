# Coordinator service example

`aipool.service.example` is a generic systemd template for a user-authorized
machine or VPS. It contains placeholders intentionally; do not commit an
edited unit, real host address, token, private key, or deployment path.

Before installing it:

1. Install the project and its virtual environment on the target machine.
2. Copy `.aipool.local.example` to the operator-local configuration location
   described in the main README and set the provider credentials there.
3. Replace every `REPLACE_WITH_*` value in a local copy of the unit.
4. Keep the gateway on loopback unless a private network and bearer token are
   configured. Put TLS and any additional access control in an appropriate
   authorized reverse proxy or tunnel.
5. Run the service through the project's deployment workflow only after testing
   `aipool serve` interactively and checking `/status` and `/stats` locally.

The unit is a supervision example, not a production hardening certificate.
Review systemd, network, firewall, provider, and data-retention settings for the
target environment. Never expose an unauthenticated coordinator or send a
provider token through a tracked file.
