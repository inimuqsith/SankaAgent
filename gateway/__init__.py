"""Local HTTP gateway between the WhatsApp bridge and Sanka.

The package is intentionally import-light: modules are imported explicitly by
the application wiring so that no import cycles appear between the server,
dispatcher, and runtime layers.
"""
