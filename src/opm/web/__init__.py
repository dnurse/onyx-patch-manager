"""Private, server-rendered OPM web interface.

The web process is only an authenticated client of the local controller. It
must never execute remote patch commands or bypass the controller's approval
and host-locking boundaries.
"""
