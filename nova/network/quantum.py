
# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 Nicira Networks
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import httplib
import json
import socket
import urllib

from nova import flags
from nova import log as logging

LOG = logging.getLogger("nova.network.quantum")
FLAGS = flags.FLAGS
FORMAT = "json"

flags.DEFINE_string('quantum_connection_host',
                    '127.0.0.1',
                    'HOST for connecting to quantum')

flags.DEFINE_string('quantum_connection_port',
                    '9696',
                    'PORT for connecting to quantum')

flags.DEFINE_string('quantum_default_tenant_id',
                    "default",
                    'Default tenant id when creating quantum networks')


def get_connection():
    host = FLAGS.quantum_connection_host
    port = FLAGS.quantum_connection_port
    return MiniClient(host, port, False)


def create_network(tenant_id, network_name):
    LOG.debug("Creating network on tenant: %s" % tenant_id)
    if tenant_id == None:
        tenant_id = FLAGS.quantum_default_tenant_id
    data = {'network': {'net-name': network_name}}
    body = json.dumps(data)
    res = get_connection().do_request(tenant_id, 'POST', "/networks." + FORMAT,
      body=body)
    if res.status != 200:
        return None
    resdict = json.loads(res.read())
    LOG.debug(resdict)
    return resdict["networks"]["network"]["id"]


def get_network(tenant_id, uuid):
    res = get_connection().do_request(tenant_id, 'GET',
      "/networks/%s.%s" % (uuid, FORMAT))
    return res.status == 200


def get_network_by_name(tenant_id, network_name):
    res = get_connection().do_request(tenant_id, 'GET', "/networks." + FORMAT)
    resdict = json.loads(res.read())
    LOG.debug(resdict)
    LOG.debug("(tenant_id: %s) Looking for name: %s" % (tenant_id,
      network_name))
    for n in resdict["networks"]:
        net_id = n["id"]
        res = get_connection().do_request(tenant_id, 'GET',
          "/networks/%s.%s" % (net_id, FORMAT))
        rd = json.loads(res.read())
        LOG.debug(rd)
        name = rd["networks"]["network"]["name"]
        LOG.debug("Network ID:%s, name: %s" % (net_id, name))
        if name == network_name:
            return net_id
    return None


def create_port(tenant_id, network_id):
    data = {'port': {'port-state': 'ACTIVE'}}
    body = json.dumps(data)
    res = get_connection().do_request(tenant_id, 'POST',
      "/networks/%s/ports.%s" % (network_id, FORMAT), body=body)
    if res.status != 200:
        return None
    resdict = json.loads(res.read())
    LOG.info(resdict)
    LOG.info("Created port on network \"%s\" with id: %s" % (network_id,
        resdict["ports"]["port"]["id"]))
    return resdict["ports"]["port"]["id"]


def plug_iface(tenant_id, network_id, port_id, interface_id):
    tid = tenant_id
    nid = network_id
    pid = port_id
    vid = interface_id
    data = {'port': {'attachment-id': '%s' % vid}}
    body = json.dumps(data)
    res = get_connection().do_request(tid, 'PUT',
      "/networks/%s/ports/%s/attachment.%s" % (nid, pid, FORMAT), body=body)
    output = res.read()
    LOG.debug(output)
    if res.status != 202:
        LOG.error("Failed to plug iface \"%s\" to port \"%s\": %s" % (vid,
          pid, output))
        return False
    LOG.info("Plugged interface \"%s\" to port:%s on network:%s" % (vid,
                                                                pid, nid))
    return True


def delete_port(tenant_id, network_id, port_id):
    res = get_connection().do_request(tenant_id, 'DELETE',
      "/networks/%s/ports/%s.%s" % (network_id, port_id, FORMAT))
    if res.status != 200:
        return False
    return True


def get_port_by_attachment(tenant_id, network_id, attachment):
    res = get_connection().do_request(tenant_id, 'GET',
      "/networks/%s/ports.%s" % (network_id, FORMAT))
    output = res.read()
    if res.status != 200:
        LOG.error("Failed to get ports for network \"%s\": %s" % (network_id,
          output))
        return None
    resdict = json.loads(output)
    for p in resdict["ports"]:
        port_id = p["id"]
        # Get port details (in order to get the attachment)
        res = get_connection().do_request(tenant_id, 'GET',
          "/networks/%s/ports/%s/attachment.%s" % (network_id, port_id,
                                                               FORMAT))
        output = res.read()
        resdict = json.loads(output)
        LOG.debug(resdict)
        port_attachment = resdict["attachment"]
        if port_attachment == attachment:
            return p["id"]
    return None


def unplug_iface(tenant_id, network_id, port_id):
    tid = tenant_id
    nid = network_id
    pid = port_id
    res = get_connection().do_request(tid, 'DELETE',
      "/networks/%s/ports/%s/attachment.%s" % (nid, pid, FORMAT))
    output = res.read()
    LOG.debug(output)
    if res.status != 202:
        LOG.error("Failed to unplug iface from port \"%s\": %s" % \
                                                    (pid, output))
        return False
    return True


class MiniClient(object):
    """A base client class - derived from Glance.BaseClient"""
    action_prefix = '/v0.1/tenants/{tenant_id}'

    def __init__(self, host, port, use_ssl):
        """
        Creates a new client to some service.

        :param host: The host where service resides
        :param port: The port where service resides
        :param use_ssl: Should we use HTTPS?
        """
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.connection = None

    def get_connection_type(self):
        """
        Returns the proper connection type
        """
        if self.use_ssl:
            return httplib.HTTPSConnection
        else:
            return httplib.HTTPConnection

    def do_request(self, tenant, method, action, body=None,
                   headers=None, params=None):
        """
        Connects to the server and issues a request.
        Returns the result data, or raises an appropriate exception if
        HTTP status code is not 2xx

        :param method: HTTP method ("GET", "POST", "PUT", etc...)
        :param body: string of data to send, or None (default)
        :param headers: mapping of key/value pairs to add as headers
        :param params: dictionary of key/value pairs to add to append
                             to action

        """
        action = MiniClient.action_prefix + action
        action = action.replace('{tenant_id}', tenant)
        if type(params) is dict:
            action += '?' + urllib.urlencode(params)

        try:
            connection_type = self.get_connection_type()
            headers = headers or {}

            # Open connection and send request
            c = connection_type(self.host, self.port)
            c.request(method, action, body, headers)
            res = c.getresponse()
            status_code = self.get_status_code(res)
            if status_code in (httplib.OK,
                               httplib.CREATED,
                               httplib.ACCEPTED,
                               httplib.NO_CONTENT):
                return res
            else:
                raise Exception("Server returned error: %s" % res.read())

        except (socket.error, IOError), e:
            raise Exception("Unable to connect to "
                            "server. Got error: %s" % e)

    def get_status_code(self, response):
        """
        Returns the integer status code from the response, which
        can be either a Webob.Response (used in testing) or httplib.Response
        """
        if hasattr(response, 'status_int'):
            return response.status_int
        else:
            return response.status
