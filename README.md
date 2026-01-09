# GARDENA smart system (cloudless) Home Assistant integration (PoC)

A proof-of-concept implementation demonstrating how a local,
WebSocket-based Home Assistant integration for the GARDENA smart system
could look like.

> [!WARNING]
> This is a largely vibe-coded implementation, NOT ready for use!

## Installation

Copy to `config/custom_components/` of your Home Assistant installation.

## Increase verbosity

To increase the log level for the integration, add the following to
`configuration.yaml`:

```yaml
 logger:
   logs:
     custom_components.gardena_smart_cloudless: debug

```
