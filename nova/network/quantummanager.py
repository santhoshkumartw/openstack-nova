# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
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

import math
import netaddr

from nova import context
from nova import db
from nova import exception
from nova import flags
from nova import ipv6
from nova import log as logging
from nova import manager
from nova import quota
from nova import utils
from nova import rpc
from nova.network import api as network_api
from nova.network import quantum
from nova.network import manager
import random

LOG = logging.getLogger("nsmanager")

FLAGS = flags.FLAGS

flags.DEFINE_string('existing_uuid', None, 'Existing quantum network uuid')

class QuantumManager(manager.FlatManager):
    def create_networks(self, context, label, cidr, multi_host, num_networks,
                        network_size, cidr_v6, gateway_v6, bridge,
                        bridge_interface, dns1=None, dns2=None, **kwargs):
        """Create networks based on parameters."""
        fixed_net = netaddr.IPNetwork(cidr)
        if FLAGS.use_ipv6:
            fixed_net_v6 = netaddr.IPNetwork(cidr_v6)
            significant_bits_v6 = 64
            network_size_v6 = 1 << 64

        for index in range(num_networks):
            start = index * network_size
            significant_bits = 32 - int(math.log(network_size, 2))
            cidr = '%s/%s' % (fixed_net[start], significant_bits)
            project_net = netaddr.IPNetwork(cidr)
            net = {}
            net['bridge'] = bridge
            net['bridge_interface'] = bridge_interface
            net['dns1'] = dns1
            net['dns2'] = dns2
            net['cidr'] = cidr
            net['multi_host'] = multi_host
            net['netmask'] = str(project_net.netmask)
            net['gateway'] = str(project_net[1])
            net['broadcast'] = str(project_net.broadcast)
            net['dhcp_start'] = str(project_net[2])
            net['priority'] = int(kwargs.get("priority", 0))
            if kwargs["project_id"] not in [None, "0"]:
                net['project_id'] = kwargs["project_id"]
            if num_networks > 1:
                net['label'] = '%s_%d' % (label, index)
            else:
                net['label'] = label

            if FLAGS.use_ipv6:
                start_v6 = index * network_size_v6
                cidr_v6 = '%s/%s' % (fixed_net_v6[start_v6],
                                     significant_bits_v6)
                net['cidr_v6'] = cidr_v6

                project_net_v6 = netaddr.IPNetwork(cidr_v6)

                if gateway_v6:
                    # use a pre-defined gateway if one is provided
                    net['gateway_v6'] = str(gateway_v6)
                else:
                    net['gateway_v6'] = str(project_net_v6[1])

                net['netmask_v6'] = str(project_net_v6._prefixlen)

            if kwargs.get('vpn', False):
                # this bit here is for vlan-manager
                del net['dns1']
                del net['dns2']
                vlan = kwargs['vlan_start'] + index
                net['vpn_private_address'] = str(project_net[2])
                net['dhcp_start'] = str(project_net[3])
                net['vlan'] = vlan
                net['bridge'] = 'br%s' % vlan

                # NOTE(vish): This makes ports unique accross the cloud, a more
                #             robust solution would be to make them uniq per ip
                net['vpn_public_port'] = kwargs['vpn_start'] + index

            # Populate the quantum network uuid if we have it.  We're
            # currently using the bridge column for this since we don't have
            # another place to put it.
            if FLAGS.existing_uuid is not None:
                try:
                    network_exists = quantum.get_network(
                      FLAGS.quantum_default_tenant_id, FLAGS.existing_uuid)
                except:
                    txt = "Unable to find quantum network with uuid: %s" % \
                      (FLAGS.existing_uuid)
                    raise Exception(txt)
                net["bridge"] = FLAGS.existing_uuid
            else:
                # If the uuid wasn't provided and the project is specified
                # then we should try to create this network via quantum.
                if kwargs.get("project_id", None) not in [None, "0"]:
                    project_id = kwargs["project_id"]
                    # We need to create the private network if it doesnt exist.
                    private_net_name = "%s_private" % (project_id)
                    net_uuid = quantum.get_network_by_name(project_id,
                      private_net_name)
                    if not net_uuid:
                        net_uuid = quantum.create_network(project_id,
                          private_net_name)
                    net["bridge"] = net_uuid
                    LOG.info(_("Quantum network uuid for network \"%s\": %s"% (
                      private_net_name, net_uuid)))

            # None if network with cidr or cidr_v6 already exists
            network = self.db.network_create_safe(context, net)

            if network:
                self._create_fixed_ips(context, network['id'])
            else:
                raise ValueError(_('Network with cidr %s already exists') %
                                   cidr)


    def _allocate_fixed_ips(self, context, instance_id, host, networks,
                            **kwargs):
        vifs = self.db.virtual_interface_get_by_instance(context, instance_id)

        return {vif['id']: melange.allocate_ip(vif['network_id'],
                                               vif['id']) for vif in vifs}
        # for network in networks:
        #     self.allocate_fixed_ip(context, instance_id, network)

    def _get_networks_for_instance(self, context, instance_id, project_id):
        """Determine & return which networks an instance should connect to."""
        networks = self.db.network_get_all(context)

        # We want to construct something like:
        #   networks = [private_network, other nets ordered by priority]
        # The private network will be the one matching the project id.  The
        # others will be networks with no project_id but with a priority key
        # that we'll use to order them.  To remove a network as a potential
        # candidate just make its priority NULL (or 0).

        LOG.debug(("Current project id: %s" % project_id))

        # Filter out any vlan networks
        networks = [network for network in networks if not network['vlan']]

        for n in networks:
            LOG.debug("%s (project: %s)" % (n["label"], n["project_id"]))
        try:
            private_network = [network for network in networks if
              network['project_id'] == project_id][0]
        except:
            raise Exception("Unable to find private network for project: %s" % (project_id))

        LOG.debug(_("Found private network: %s" % private_network))

        result = [private_network]
        # Filter out any networks without a priority
        networks_with_pri = []
        for x in networks:
            pri = 0
            try:
                pri = int(x["priority"])
            except:
                continue
            if pri == 0:
                continue
            networks_with_pri.append(x)
            LOG.debug(_("Found network with priority %d: %s" % (pri,
              x["label"])))
        networks_with_pri.sort(key=lambda x: x["priority"])
        for x in networks_with_pri:
            result.append(x)
        return result

    def allocate_for_instance(self, context, **kwargs):
        """Handles allocating the various network resources for an instance.

        rpc.called by network_api
        """
        instance_id = kwargs.pop('instance_id')
        host = kwargs.pop('host')
        project_id = kwargs.pop('project_id')
        type_id = kwargs.pop('instance_type_id')
        vpn = kwargs.pop('vpn')
        admin_context = context.elevated()
        LOG.debug(_("network allocations for instance %s"), instance_id,
                                                            context=context)
        networks = self._get_networks_for_instance(admin_context, instance_id,
                                                                  project_id)
        LOG.warn(networks)
        self._allocate_mac_addresses(context, instance_id, networks)
        self._allocate_fixed_ips(admin_context, instance_id, host, networks,
          vpn=vpn)
        return self.get_instance_nw_info(context, instance_id, type_id, host)

    def get_instance_nw_info(self, context, instance_id, instance_type_id, host,
                             ips=None, **kwargs):
        """Creates network info list for instance.

        called by allocate_for_instance and netowrk_api
        context needs to be elevated
        :returns: network info list [(network,info),(network,info)...]
        where network = dict containing pertinent data from a network db object
        and info = dict containing pertinent networking data
        """
        # TODO(tr3buchet) should handle floating IPs as well?
        fixed_ips = self.db.fixed_ip_get_by_instance(context, instance_id)
        vifs = self.db.virtual_interface_get_by_instance(context, instance_id)
        flavor = self.db.instance_type_get_by_id(context,
                                                 instance_type_id)
        network_info = []
        # a vif has an address, instance_id, and network_id
        # it is also joined to the instance and network given by those IDs
        for vif in vifs:
            ips_for_vif = ips[vif["id"]]
            v4_ips = [ip for ip in ips_for_vif if netaddr.IPAddress(ip["address"].version == 4]
            v6_ips = [ip for ip in ips_for_vif if netaddr.IPAddress(ip["address"].version == 6]
            network = vif['network']
            
            # TODO(tr3buchet) eventually "enabled" should be determined
            def ip_dict(ip):
                return {
                    "ip": ip["address"],
                    "netmask": ip["netmask"],
                    "enabled": "1"}

            network_dict = {
                'bridge': network['bridge'],
                'id': network['id'],
                'cidr': network['cidr'],
                'cidr_v6': network['cidr_v6'],
                'injected': network['injected']}
            info = {
                'label': network['label'],
                'gateway': v4_ips[0]['gateway'],
                'broadcast': network['broadcast'],
                'mac': vif['address'],
                'rxtx_cap': flavor['rxtx_cap'],
                'dns': [network['dns']],
                'ips': [ip_dict(ip) for ip in v4_ips)]
            if network['cidr_v6']:
                info['ip6s'] = [ip_dict(ip) for ip in v6_ips)]
            # TODO(tr3buchet): handle ip6 routes here as well
            if network['gateway_v6']:
                info['gateway6'] = v6_ips[0]['gateway_v6']
            network_info.append((network_dict, info))
        return network_info
