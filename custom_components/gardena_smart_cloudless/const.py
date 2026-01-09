import ssl

DOMAIN = "gardena_smart_system"

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8443

CONF_PASSWORD = "password"

COMMAND_HUM = 2

# Model-specific temperature commands
COMMAND_TEMP_18845 = 5  # Model 18845 temperature measurement
COMMAND_TEMP_19040 = 3  # Model 19040 temperature measurement (also default)


def create_insecure_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that doesn't verify certificates."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
