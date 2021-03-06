import os
import logging
from proxymatic import util

class HAProxyBackend(object):
    def __init__(self, maxconnections, statusendpoint, pidfile='/run/haproxy.pid'):
        self._maxconnections = maxconnections
        self._statusendpoint = statusendpoint
        self._pidfile = pidfile
        self._prev = {}
        self._cfgfile = '/etc/haproxy/haproxy.cfg'
        self._render({})

    def update(self, source, services):
        # HAproxy only supports TCP
        accepted = {}
        for key, service in services.items():
            if service.protocol == 'tcp' or service.protocol == 'unix':
                accepted[key] = service

        # Check if anything has changed
        if self._prev != accepted:
            self._render(accepted)

            # Instruct HAproxy to reload the config
            logging.debug("Reloaded the HAproxy config '%s'", self._cfgfile)
            command = 'haproxy -f %s -p %s' % (self._cfgfile, self._pidfile)
            if os.path.exists(self._pidfile):
                command += ' -sf ' + open(self._pidfile).read()
            util.shell(command)

            # Remember the services that were rendered
            self._prev = accepted

        return accepted

    def _render(self, accepted):
        # Expand the config template
        util.renderTemplate('/etc/haproxy/haproxy.cfg.tpl', self._cfgfile, {
            'services': accepted,
            'maxconnections': self._maxconnections,
            'statusendpoint': self._statusendpoint})
