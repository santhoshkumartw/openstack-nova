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

class NetstackManager(manager.FlatManager):
    def create_networks(self, context, label, cidr, num_networks,
                        network_size, cidr_v6, gateway_v6, bridge,
                        bridge_interface, **kwargs):
        """Create networks based on parameters."""
        fixed_net = netaddr.IPNetwork(cidr)
        fixed_net_v6 = netaddr.IPNetwork(cidr_v6)
        significant_bits_v6 = 64
        network_size_v6 = 1 << 64
        count = 0
        for index in range(num_networks):
            start = index * network_size
            start_v6 = index * network_size_v6
            significant_bits = 32 - int(math.log(network_size, 2))
            cidr = '%s/%s' % (fixed_net[start], significant_bits)
            project_net = netaddr.IPNetwork(cidr)
            net = {}
            net['bridge'] = bridge
            net['bridge_interface'] = bridge_interface
            net['dns'] = FLAGS.flat_network_dns
            net['cidr'] = cidr
            net['netmask'] = str(project_net.netmask)
            net['gateway'] = str(project_net[1])
            net['broadcast'] = str(project_net.broadcast)
            net['dhcp_start'] = str(project_net[2])
            net['priority'] = kwargs.get("priority", None)
            if kwargs["project_id"] not in [None, "0"]:
                net['project_id'] = kwargs["project_id"]
            count += 1
            if num_networks > 1:
                net['label'] = '%s_%d' % (label, index)
            else:
                net['label'] = label

            if FLAGS.use_ipv6:
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
                del net['dns']
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

            # None if network with cidr or cidr_v6 already exists
            network = self.db.network_create_safe(context, net)

            if network:
                self._create_fixed_ips(context, network['id'])
            else:
                raise ValueError(_('Network with cidr %s already exists') %
                                   cidr)


    def _allocate_fixed_ips(self, context, instance_id, networks):
        for network in networks:
            self.allocate_fixed_ip(context, instance_id, network)

    def _get_networks_for_instance(self, context, instance_id, project_id):
        """Determine & return which networks an instance should connect to."""
        networks = self.db.network_get_all(context)

        # We want to construct something like:
        #   networks = [private_network, other nets ordered by priority]
        # The private network will be the one matching the project id.  The
        # others will be networks with no project_id but with a priority key
        # that we'll use to order them.  To remove a network as a potential
        # candidate just make its priority NULL (or 0).

        LOG.debug("Current project id: %s" % project_id)

        # Filter out any vlan networks and any networks that don't have the
        # host set
        networks = [network for network in networks if
                not network['vlan'] and network['host']]

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
        project_id = kwargs.pop('project_id')
        type_id = kwargs.pop('instance_type_id')
        admin_context = context.elevated()
        LOG.debug(_("network allocations for instance %s"), instance_id,
                                                            context=context)
        networks = self._get_networks_for_instance(admin_context, instance_id,
                                                                  project_id)
        self._allocate_mac_addresses(context, instance_id, networks)
        self._allocate_fixed_ips(admin_context, instance_id, networks)
        return self.get_instance_nw_info(context, instance_id, type_id)

