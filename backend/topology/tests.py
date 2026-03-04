"""Topology test entrypoint.

Legacy direct-SNMP tests were removed after the Zabbix-only collector migration.
Keep this module as the default Django test discovery target.
"""

from topology.tests_zabbix_mode import *  # noqa: F401,F403
