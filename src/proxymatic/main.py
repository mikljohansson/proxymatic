#!/usr/bin/env python
import os
import sys
import signal
import optparse
import logging
import time
from proxymatic.discovery.aggregate import AggregateDiscovery
from proxymatic.discovery.marathon import MarathonDiscovery
from proxymatic.discovery.registrator import RegistratorEtcdDiscovery
from proxymatic.backend.aggregate import AggregateBackend
from proxymatic.backend.haproxy import HAProxyBackend
from proxymatic.backend.nginx import NginxBackend
from proxymatic.backend.pen import PenBackend
from proxymatic.status import StatusEndpoint

parser = optparse.OptionParser(
    usage='docker run meltwater/proxymatic:latest [options]...',
    description='Proxy for TCP/UDP services registered in Marathon and etcd')

def parsebool(value):
    truevals = set(['true', '1'])
    falsevals = set(['false', '0'])
    stripped = str(value).lower().strip()
    if stripped in truevals:
        return True
    if stripped in falsevals:
        return False

    logging.error("Invalid boolean value '%s'", value)
    sys.exit(1)

def parseint(value):
    try:
        return int(value)
    except:
        logging.error("Invalid integer value '%s'", value)
        sys.exit(1)

def parselist(value):
    return filter(bool, value.split(','))


parser.add_option('-m', '--marathon', dest='marathon', help='List of Marathon replicas, e.g. "http://marathon-01:8080/,http://marathon-02:8080/"',
                  default=os.environ.get('MARATHON_URL', ''))
parser.add_option('-c', '--marathon-callback', dest='callback',
                  help='[DEPRECATED] URL to listen for Marathon HTTP callbacks, e.g. "http://`hostname -f`:5090/"',
                  default=os.environ.get('MARATHON_CALLBACK_URL', None))

parser.add_option('-r', '--registrator', dest='registrator',
                  help='URL where registrator publishes services, e.g. "etcd://etcd-host:4001/services"',
                  default=os.environ.get('REGISTRATOR_URL', None))

parser.add_option('-i', '--refresh-interval', dest='interval',
                  help='Polling interval in seconds when using non-event capable backends [default: %default]',
                  type="int", default=parseint(os.environ.get('REFRESH_INTERVAL', '60')))
parser.add_option('-e', '--expose-host', dest='exposehost',
                  help='Expose services running in net=host mode [default: %default]',
                  action="store_true", default=parsebool(os.environ.get('EXPOSE_HOST', False)))

parser.add_option('--status-endpoint', dest='statusendpoint',
                  help='Expose /status endpoint and HAproxy stats on this ip:port [default: %default]. Specify an empty string to disable this endpoint',
                  default=os.environ.get('STATUS_ENDPOINT', '0.0.0.0:9090'))

parser.add_option('--group-size', dest='groupsize',
                  help='Number of Proxymatic instances serving this cluster. Per container connection ' +
                       'limits are divided by this number to ensure a globally coordinated maxconn per container [default: %default]',
                  type="int", default=parseint(os.environ.get('GROUP_SIZE', '1')))

parser.add_option('--max-connections', dest='maxconnections',
                  help='Max number of connection per service [default: %default]',
                  type="int", default=parseint(os.environ.get('MAX_CONNECTIONS', '8192')))

parser.add_option('--pen-servers', dest='penservers', help='Max number of backends for each service [default: %default]',
                  type="int", default=parseint(os.environ.get('PEN_SERVERS', '64')))
parser.add_option('--pen-clients', dest='penclients', help='Max number of connection tracked clients [default: %default]',
                  type="int", default=parseint(os.environ.get('PEN_CLIENTS', '8192')))

parser.add_option('--haproxy', dest='haproxy', help='Use HAproxy for TCP services instead of running everything through Pen [default: %default]',
                  action="store_true", default=parsebool(os.environ.get('HAPROXY', True)))

parser.add_option('--vhost-domain', dest='vhostdomain', help='Domain to add service virtual host under, e.g. "services.example.com"',
                  default=os.environ.get('VHOST_DOMAIN', None))
parser.add_option('--vhost-port', dest='vhostport', help='Port to serve virtual hosts from [default: %default]"',
                  type="int", default=parseint(os.environ.get('VHOST_PORT', '80')))
parser.add_option('--proxy-protocol', dest='proxyprotocol', help='Enable proxy protocol on the nginx vhost [default: %default]',
                  action="store_true", default=parsebool(os.environ.get('PROXY_PROTOCOL', False)))

parser.add_option('-v', '--verbose', dest='verbose', help='Increase logging verbosity',
                  action="store_true", default=parsebool(os.environ.get('VERBOSE', False)))

(options, args) = parser.parse_args()

# Use timestamps in log messages
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')

# Optionally increase log level
if options.verbose:
    logging.getLogger().setLevel(logging.DEBUG)

if not options.registrator and not options.marathon:
    parser.print_help()
    sys.exit(1)

backend = AggregateBackend(options.exposehost)

if options.vhostdomain:
    backend.add(NginxBackend(options.vhostport, options.vhostdomain, options.proxyprotocol, options.maxconnections))

# Option indicates preferance of HAproxy for TCP services
haproxy = HAProxyBackend(options.maxconnections, options.statusendpoint)
if options.haproxy:
    backend.add(haproxy)

# Pen is needed for UDP support so always add it
backend.add(PenBackend(options.maxconnections, options.penservers, options.penclients))

# Add the HAproxy backend to handle the Marathon unix socket
if not options.haproxy:
    backend.add(haproxy)

discovery = AggregateDiscovery()
if options.registrator:
    registrator = RegistratorEtcdDiscovery(backend, options.registrator)
    registrator.start()
    discovery.add(registrator)

if options.marathon:
    marathon = MarathonDiscovery(backend, parselist(options.marathon), options.interval, options.groupsize)
    marathon.start()
    discovery.add(marathon)

# Start status endpoints
status = StatusEndpoint(discovery)
status.start()

# Trap signals and start failing the status endpoint to allow upstream load balancers to
# detect that this Proxymatic instance is stopping.
def sigterm_handler(_signo, _stack_frame):
    global status
    status.terminate()


signal.signal(signal.SIGTERM, sigterm_handler)

# Loop forever and allow the threads to work. Setting the threads to daemon=False and returning
# from the main thread seems to prevent Ctrl+C/SIGTERM from terminating the process properly.
while True:
    time.sleep(60)
