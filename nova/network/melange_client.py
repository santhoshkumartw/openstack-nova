# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 OpenStack LLC.
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
import socket
import urllib
import json
from nova import flags


FLAGS = flags.FLAGS

flags.DEFINE_string('melange_host',
                    '127.0.0.1',
                    'HOST for connecting to melange')

flags.DEFINE_string('melange_port',
                    '9898',
                    'PORT for connecting to melange')


def allocate_ip(network_id, vif_id, project_id=None):
    tenant_scope = "/tenants/%s" % project_id if project_id else ""

    url = ("/v0.1/ipam/networks/%(network_id)s%(tenant_scope)s/ports/%(vif_id)s/ip_allocations" %
               locals())

    client = Client(FLAGS.melange_host, FLAGS.melange_port)
    response = client.post(url, headers={'Content-type':"application/json"})
    return json.loads(response.read())['ip_addresses']


def create_block(network_id, cidr, project_id=None):
    tenant_scope = "/tenants/%s" % project_id if project_id else ""

    url = "/v0.1/ipam%(tenant_scope)s/ip_blocks" % locals()
    
    client = Client(FLAGS.melange_host, FLAGS.melange_port)
    client.post(url, body=json.dumps(dict(ip_block=dict(cidr=cidr, network_id=network_id, type='private'))),
                headers={'Content-type':"application/json"})



class Client(object):

    def __init__(self, host='localhost', port=8080, use_ssl=False):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl

    def get(self, path, params={}, headers={}):
        return self.do_request("GET", path, params=params, headers=headers)

    def post(self, path, body=None, headers={}):
        return self.do_request("POST", path, body=body, headers=headers)

    def delete(self, path, headers={}):
        return self.do_request("DELETE", path, headers=headers)

    def _get_connection(self):
        if self.use_ssl:
            return httplib.HTTPSConnection(self.host, self.port)
        else:
            return httplib.HTTPConnection(self.host, self.port)

    def do_request(self, method, path, body=None, headers={}, params={}):

        url = path + '?' + urllib.urlencode(params)

        try:
            connection = self._get_connection()
            connection.request(method, url, body, headers)
            response = connection.getresponse()
            if response.status < 400:
                return response
            raise Exception("Server returned error: %s", response.read())
        except (socket.error, IOError), e:
            raise Exception("Unable to connect to "
                            "server. Got error: %s" % e)

